"""Diagnostic reasoning agent and patch synthesizer.
Analyzes execution failure evidence, identifies root causes, and proposes structured patches
bound with cryptographic SHA-256 hashes.
"""

import hashlib
import uuid
from typing import Any, Dict, List, Optional
from zenkai.db import canonical_json
from zenkai.models import PatchProposal, PatchType


def compute_proposal_hash(patch_payload: Dict[str, Any], incident_id: str, schema_version: str = "v2.4") -> str:
    """Computes deterministic SHA-256 hash over canonical JSON of (patch_payload + incident_id + schema_version)."""
    raw = canonical_json({
        "incident_id": incident_id,
        "patch_payload": patch_payload,
        "schema_version": schema_version,
    })
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DiagnosticAgent:
    """Diagnoses workflow exceptions and synthesizes constrained field-mapping or recovery patches."""

    def diagnose_and_propose(
        self,
        incident_id: str,
        failed_payload: Dict[str, Any],
        error_code: str,
        error_message: str,
        error_details: Optional[Dict[str, Any]] = None,
        schema_version: str = "v2.4",
    ) -> PatchProposal:
        """Generates a structured diagnosis and candidate patch from observed error telemetry."""
        error_details = error_details or {}

        # Scenario 1: Schema Drift / Field Rename
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
            )

        # Scenario 2: Network Timeout / Transient Gateway Failure
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
        )

    def generate_unsafe_patch(self, incident_id: str, failed_payload: Dict[str, Any], schema_version: str = "v2.4") -> PatchProposal:
        """Injects a simulated hallucinated / malicious proposal to test the deterministic validator."""
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
        )
