"""Diagnostic reasoning agent and patch synthesizer.
Supports LLM_MODE=mock|live (default mock). Live calls use bounded timeouts and token limits
with strict Pydantic schema validation and automatic fallback to mock on error.
Synthetic unsafe patches are explicitly labelled with injected_test_input=True.
"""

import hashlib
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field, ValidationError

from zenkai.db import canonical_json
from zenkai.models import PatchProposal, PatchType

logger = logging.getLogger("zenkai.diagnostics")


def compute_proposal_hash(patch_payload: Dict[str, Any], incident_id: str, schema_version: str = "v2.4") -> str:
    """Computes deterministic SHA-256 hash over canonical JSON of (patch_payload + incident_id + schema_version)."""
    raw = canonical_json({
        "incident_id": incident_id,
        "patch_payload": patch_payload,
        "schema_version": schema_version,
    })
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LiveModelPatchOutput(BaseModel):
    """Strict schema for validating live model structured response."""
    root_cause: str = Field(min_length=5)
    evidence_citations: List[str] = Field(min_length=1)
    patch_type: PatchType
    field_mappings: Dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class DiagnosticAgent:
    """Diagnoses workflow exceptions and synthesizes constrained field-mapping or recovery patches."""

    def __init__(self, llm_mode: Optional[str] = None):
        self.llm_mode = (llm_mode or os.getenv("LLM_MODE", "mock")).lower()
        self.api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free")
        self.base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "5.0"))
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "500"))

    def diagnose_and_propose(
        self,
        incident_id: str,
        failed_payload: Dict[str, Any],
        error_code: str,
        error_message: str,
        error_details: Optional[Dict[str, Any]] = None,
        schema_version: str = "v2.4",
    ) -> PatchProposal:
        """Generates a structured diagnosis and candidate patch.
        If LLM_MODE=live, queries model with strict Pydantic validation,
        falling back automatically to mock on error or invalid output.
        """
        if self.llm_mode == "live":
            live_proposal = self._try_live_diagnosis(
                incident_id=incident_id,
                failed_payload=failed_payload,
                error_code=error_code,
                error_message=error_message,
                schema_version=schema_version,
            )
            if live_proposal:
                return live_proposal

        # Default Mock / Fallback deterministic diagnostic reasoning
        return self._mock_diagnose(
            incident_id=incident_id,
            failed_payload=failed_payload,
            error_code=error_code,
            error_message=error_message,
            schema_version=schema_version,
        )

    def _try_live_diagnosis(
        self,
        incident_id: str,
        failed_payload: Dict[str, Any],
        error_code: str,
        error_message: str,
        schema_version: str,
    ) -> Optional[PatchProposal]:
        """Queries live LLM with bounded timeout, max tokens, and strict Pydantic validation."""
        if not self.api_key:
            logger.warning("LLM_MODE=live requested but no API key configured. Falling back to deterministic mock.")
            return None

        prompt = (
            f"You are a self-healing automation diagnostic agent.\n"
            f"API Contract Target Schema: {schema_version}\n"
            f"Failed Request Payload: {json.dumps(failed_payload)}\n"
            f"Error Code: {error_code}\n"
            f"Error Message: {error_message}\n\n"
            f"Diagnose the root cause and propose a safe field mapping. Return JSON adhering to:\n"
            f'{{"root_cause": str, "evidence_citations": [str], "patch_type": "FIELD_MAPPING"|"RETRY_WITH_RECONCILIATION", '
            f'"field_mappings": dict, "confidence": float}}'
        )

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self.max_tokens,
                "response_format": {"type": "json_object"},
            }
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
                if res.status_code != 200:
                    logger.warning(f"Live LLM returned status {res.status_code}. Falling back to mock.")
                    return None

                raw_json = res.json()["choices"][0]["message"]["content"]
                parsed_data = json.loads(raw_json)

            # Strict Pydantic Schema Validation
            validated_output = LiveModelPatchOutput.model_validate(parsed_data)

            # Construct patched payload according to validated mappings
            patched_payload = dict(failed_payload)
            for src, dst in validated_output.field_mappings.items():
                if src in patched_payload:
                    val = patched_payload.pop(src)
                    patched_payload[dst] = val

            proposal_hash = compute_proposal_hash(patched_payload, incident_id, schema_version)

            return PatchProposal(
                patch_id=f"PATCH-LIVE-{uuid.uuid4().hex[:8].upper()}",
                incident_id=incident_id,
                root_cause=validated_output.root_cause,
                evidence_citations=validated_output.evidence_citations,
                patch_type=validated_output.patch_type,
                field_mappings=validated_output.field_mappings,
                original_payload=failed_payload,
                patched_payload=patched_payload,
                requires_human_approval=True,
                confidence=validated_output.confidence,
                schema_version=schema_version,
                proposal_hash=proposal_hash,
                injected_test_input=False,
            )

        except (httpx.RequestError, httpx.TimeoutException) as net_err:
            logger.warning(f"Live LLM network error / timeout: {net_err}. Falling back to mock.")
            return None
        except (json.JSONDecodeError, KeyError, ValidationError) as parse_err:
            logger.warning(f"Live LLM output failed strict Pydantic validation: {parse_err}. Output rejected.")
            return None

    def _mock_diagnose(
        self,
        incident_id: str,
        failed_payload: Dict[str, Any],
        error_code: str,
        error_message: str,
        schema_version: str,
    ) -> PatchProposal:
        """Deterministic mock diagnostic reasoning."""
        if "CONTRACT_DRIFT_ERR" in error_code or "SCHEMA_MISMATCH" in error_code or "semester_id" in str(error_message):
            citations = [
                f"API contract schema {schema_version} requires field 'term_id'.",
                "Client payload supplied deprecated field 'semester_id' with value: " + str(failed_payload.get("semester_id")),
                "Connector catalog defines semantic equivalence: semester_id <-> term_id"
            ]

            patched_payload = dict(failed_payload)
            term_value = patched_payload.pop("semester_id", None)
            if term_value:
                patched_payload["term_id"] = term_value

            proposal_hash = compute_proposal_hash(patched_payload, incident_id, schema_version)

            return PatchProposal(
                patch_id=f"PATCH-{uuid.uuid4().hex[:8].upper()}",
                incident_id=incident_id,
                root_cause=f"Schema drift between client portal and Registration ERP {schema_version}: field 'semester_id' was deprecated in favor of 'term_id'.",
                evidence_citations=citations,
                patch_type=PatchType.FIELD_MAPPING,
                field_mappings={"semester_id": "term_id"},
                original_payload=failed_payload,
                patched_payload=patched_payload,
                requires_human_approval=True,
                confidence=0.98,
                schema_version=schema_version,
                proposal_hash=proposal_hash,
                injected_test_input=False,
            )

        elif "GATEWAY_TIMEOUT" in error_code or "504" in str(error_code):
            citations = [
                "HTTP 504 Gateway Timeout encountered on ERP registration endpoint.",
                "Original idempotency key preserved to ensure zero duplicate enrollments.",
                "Service policy permits bounded retry with idempotent verification."
            ]
            proposal_hash = compute_proposal_hash(failed_payload, incident_id, schema_version)
            return PatchProposal(
                patch_id=f"PATCH-{uuid.uuid4().hex[:8].upper()}",
                incident_id=incident_id,
                root_cause="Network socket timeout communicating with registration ERP. Downstream write status is uncertain.",
                evidence_citations=citations,
                patch_type=PatchType.RETRY_WITH_RECONCILIATION,
                field_mappings={},
                original_payload=failed_payload,
                patched_payload=dict(failed_payload),
                requires_human_approval=False,
                confidence=0.92,
                schema_version=schema_version,
                proposal_hash=proposal_hash,
                injected_test_input=False,
            )

        # Fallback generic proposal
        proposal_hash = compute_proposal_hash(failed_payload, incident_id, schema_version)
        return PatchProposal(
            patch_id=f"PATCH-{uuid.uuid4().hex[:8].upper()}",
            incident_id=incident_id,
            root_cause=f"Unclassified failure: {error_message}",
            evidence_citations=[error_message],
            patch_type=PatchType.ROUTE_REPLACEMENT,
            field_mappings={},
            original_payload=failed_payload,
            patched_payload=dict(failed_payload),
            requires_human_approval=True,
            confidence=0.50,
            schema_version=schema_version,
            proposal_hash=proposal_hash,
            injected_test_input=False,
        )

    def generate_unsafe_patch(self, incident_id: str, failed_payload: Dict[str, Any], schema_version: str = "v2.4") -> PatchProposal:
        """Injects a simulated hallucinated / malicious proposal to test the deterministic validator.
        Explicitly marked with injected_test_input=True.
        """
        malicious_payload = dict(failed_payload)
        malicious_payload["student_id"] = "STU-ROGUE-999"
        malicious_payload["bypass_fee"] = True
        malicious_payload["term_id"] = "MUTATED-TERM-X"

        proposal_hash = compute_proposal_hash(malicious_payload, incident_id, schema_version)

        return PatchProposal(
            patch_id=f"UNSAFE-PATCH-{uuid.uuid4().hex[:8].upper()}",
            incident_id=incident_id,
            root_cause="INJECTED UNSAFE PROPOSAL (Simulated AI Hallucination / Privilege Escalation Attack)",
            evidence_citations=["Injected synthetic test payload violating safety invariants"],
            patch_type=PatchType.UNSAFE_MODIFICATION,
            field_mappings={"semester_id": "term_id", "bypass_fee": "bypass_fee"},
            original_payload=failed_payload,
            patched_payload=malicious_payload,
            requires_human_approval=True,
            confidence=0.30,
            schema_version=schema_version,
            proposal_hash=proposal_hash,
            injected_test_input=True,
        )
