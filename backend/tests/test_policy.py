import pytest
from pydantic import ValidationError

from zenkai.domain import Proposal, proposal_hash, redact, validate_proposal


def proposal(**changes):
    return Proposal(**({"root_cause": "Gateway upstream port differs from the registered inventory",
                       "evidence_ids": ["log-1"], "action": "restore_upstream", "target": "gateway",
                       "upstream_port": 8000, "schema_field": None, "capacity": None,
                       "verification_plan": "Probe correct content under continued traffic"} | changes))


@pytest.fixture
def snapshot():
    return {"gateway": {"upstream_port": 8999, "revision": 2},
            "portal": {"running": True, "provider_schema": "v1", "adapter_field": "services", "capacity": 24}}


def test_known_good_upstream(snapshot):
    assert all(item["passed"] for item in validate_proposal(proposal(), [{"id": "log-1"}], snapshot))


@pytest.mark.parametrize("changes", [{"upstream_port": 22}, {"target": "portal"},
                                      {"capacity": 1000}, {"evidence_ids": ["made-up"]}])
def test_unsafe_proposals_are_rejected(snapshot, changes):
    with pytest.raises(ValueError):
        validate_proposal(proposal(**changes), [{"id": "log-1"}], snapshot)


def test_no_extra_commands():
    with pytest.raises(ValidationError):
        proposal(command="rm -rf /")


def test_running_process_cannot_be_restarted(snapshot):
    with pytest.raises(ValueError):
        validate_proposal(proposal(action="restart_service", target="portal", upstream_port=None),
                          [{"id": "log-1"}], snapshot)


def test_revision_is_part_of_approval_hash():
    assert proposal_hash(proposal().model_dump(), {"revision": 1}) != proposal_hash(proposal().model_dump(), {"revision": 2})


def test_secret_redaction():
    assert "super-secret" not in str(redact({"log": "super-secret sk-or-abc123 postgresql://u:p@host/db"}, ["super-secret"]))
    assert "u:p" not in str(redact({"log": "postgresql://u:p@host/db"}))
