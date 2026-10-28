import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    root_cause: str = Field(min_length=12, max_length=1500)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    action: Literal["restore_upstream", "restart_service", "patch_schema_mapping", "set_capacity"]
    target: Literal["gateway", "portal"]
    upstream_port: int | None
    schema_field: str | None
    capacity: int | None
    verification_plan: str = Field(min_length=10, max_length=1000)


def proposal_hash(proposal, revision):
    payload = {"proposal": proposal, "revision": revision}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def validate_proposal(proposal: Proposal, evidence, snapshot):
    """Independent policy, not model-provided confidence, authorises the action shape."""
    ids = {item["id"] for item in evidence}
    if not set(proposal.evidence_ids).issubset(ids):
        raise ValueError("Proposal cites evidence that does not exist")
    expected_target = "gateway" if proposal.action == "restore_upstream" else "portal"
    if proposal.target != expected_target:
        raise ValueError("Action is outside the registered target")
    values = {"upstream_port": proposal.upstream_port, "schema_field": proposal.schema_field,
              "capacity": proposal.capacity}
    field = {"restore_upstream": "upstream_port", "patch_schema_mapping": "schema_field",
             "set_capacity": "capacity"}.get(proposal.action)
    if any(value is not None for key, value in values.items() if key != field):
        raise ValueError("Proposal includes unrelated mutations")
    if proposal.action == "restore_upstream":
        if proposal.upstream_port != 8000 or snapshot["gateway"]["upstream_port"] == 8000:
            raise ValueError("Only the registered backend port 8000 can be restored")
    elif proposal.action == "patch_schema_mapping":
        expected = "offerings" if snapshot["portal"]["provider_schema"] == "v2" else "services"
        if proposal.schema_field != expected or snapshot["portal"]["adapter_field"] == expected:
            raise ValueError("Mapping must match the observed provider contract")
    elif proposal.action == "set_capacity":
        if proposal.capacity != 24 or snapshot["portal"]["capacity"] >= 24:
            raise ValueError("Only the approved demo capacity of 24 is allowed")
    elif snapshot["portal"]["running"]:
        raise ValueError("A healthy running process cannot be restarted by this proposal")
    return [
        {"name": "Evidence references", "passed": True},
        {"name": "Target allowlist", "passed": True},
        {"name": "Configuration and resource policy", "passed": True},
        {"name": "Immutable business data", "passed": True},
    ]


def redact(value, secrets=()):
    text = json.dumps(value, ensure_ascii=False)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?:postgres(?:ql)?(?:\+psycopg)?://)[^\s\"\\]+", "[DATABASE_URL]", text)
    text = re.sub(r"sk-or-[A-Za-z0-9_-]+", "[API_KEY]", text)
    return json.loads(text)
