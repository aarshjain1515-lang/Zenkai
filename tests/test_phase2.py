"""Tests for Phase 2: Hot-patch pre-dispatch safety, schema version guardrails,
LLM_MODE mock/live validation, and injected_test_input labelling.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from zenkai.diagnostics import DiagnosticAgent
from zenkai.engine import ZenkaiEngine
from zenkai.models import (
    Course,
    FailureScenario,
    IncidentStatus,
    StepName,
    StepStatus,
    Student,
)


@pytest.fixture
def temp_engine():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    engine = ZenkaiEngine(db_path=db_path)
    yield engine
    try:
        os.remove(db_path)
    except OSError:
        pass


def test_invalid_hot_patched_payload_blocked_and_adapter_disabled(temp_engine: ZenkaiEngine):
    """Pre-dispatch validation runs on hot-patched payloads; invalid payloads are blocked and disable the adapter."""
    # Deliberately seed a forbidden/malicious mapping into adapters table
    temp_engine.db.save_adapter(src_field="semester_id", dst_field="bypass_fee", schema_version="v2.4", created_at="2026-10-28T12:00:00Z")
    temp_engine.active_adapters = temp_engine.db.list_adapters()
    assert "semester_id" in temp_engine.active_adapters

    student = Student(student_id="STU-7801", name="Safety Student", email="s@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.NONE)
    result = temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    # Workflow step must halt with ADAPTER_DISABLED
    assert result.steps[StepName.REGISTRATION].status == StepStatus.ADAPTER_DISABLED
    assert result.status == StepStatus.FAILED

    # The unsafe adapter must be purged and disabled from DB and memory
    assert "semester_id" not in temp_engine.active_adapters
    assert "semester_id" not in temp_engine.db.list_adapters()

    # Timeline events must confirm disabled adapter
    events = temp_engine.db.list_timeline_events()
    disabled_events = [e for e in events if e["event_type"] == "HOT_PATCH_VALIDATION_FAILED_DISABLED"]
    assert len(disabled_events) >= 1
    assert disabled_events[0]["details"]["disabled_adapter"] == "semester_id"


def test_version_mismatch_prevents_hot_patch_reuse(temp_engine: ZenkaiEngine):
    """Adapters registered for a different contract version cannot be reused on target schema v2.4."""
    # Seed an adapter registered under legacy v1.0
    temp_engine.db.save_adapter(src_field="semester_id", dst_field="term_id", schema_version="v1.0", created_at="2026-10-28T12:00:00Z")
    temp_engine.active_adapters = temp_engine.db.list_adapters()

    student = Student(student_id="STU-7802", name="Version Student", email="v@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    # Workflow uses legacy payload with semester_id; target expects v2.4
    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.NONE)
    temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=False)

    # Event log must indicate adapter was skipped due to schema version mismatch
    events = temp_engine.db.list_timeline_events()
    mismatch_events = [e for e in events if e["event_type"] == "HOT_PATCH_SKIPPED_SCHEMA_VERSION_MISMATCH"]
    assert len(mismatch_events) >= 1
    assert mismatch_events[0]["details"]["adapter_schema_version"] == "v1.0"
    assert mismatch_events[0]["details"]["target_schema_version"] == "v2.4"


def test_malformed_live_output_rejected_and_falls_back():
    """Live LLM mode strictly validates structured output with Pydantic; malformed responses fall back safely."""
    agent = DiagnosticAgent(llm_mode="live")
    agent.api_key = "test_mock_key"

    # Simulate live API returning malformed / missing-field JSON
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": '{"broken_json_schema": true}'  # Missing required root_cause & patch_type
            }
        }]
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        proposal = agent.diagnose_and_propose(
            incident_id="INC-TEST-MALFORMED",
            failed_payload={"semester_id": "SEM-2026-FALL", "student_id": "STU-8821"},
            error_code="CONTRACT_DRIFT_ERR",
            error_message="Missing required field 'term_id'",
        )

        # Fallback to mock occurs cleanly; invalid live output is never returned
        assert proposal is not None
        assert proposal.field_mappings == {"semester_id": "term_id"}
        assert "Schema drift" in proposal.root_cause


def test_injected_test_input_chaos_patches_carry_label(temp_engine: ZenkaiEngine):
    """Every synthetic unsafe patch must be explicitly flagged with injected_test_input=True."""
    student = Student(student_id="STU-7804", name="Chaos Student", email="c@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.UNSAFE_PROPOSAL)
    res = temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    inc = temp_engine.get_incident(res.incident_id)
    assert inc is not None
    assert inc.injected_test_input is True
    assert inc.patch_proposal is not None
    assert inc.patch_proposal.injected_test_input is True

    # Verify event logs also carry injected_test_input=True
    events = temp_engine.db.list_timeline_events()
    unsafe_events = [e for e in events if e.get("details", {}).get("injected_test_input") is True]
    assert len(unsafe_events) >= 1


def test_suite_passes_with_llm_mode_mock_and_no_network():
    """Default LLM_MODE=mock runs 100% offline with zero network dependency."""
    agent = DiagnosticAgent(llm_mode="mock")
    assert agent.llm_mode == "mock"

    proposal = agent.diagnose_and_propose(
        incident_id="INC-OFFLINE-TEST",
        failed_payload={"semester_id": "SEM-2026-FALL", "student_id": "STU-8821"},
        error_code="CONTRACT_DRIFT_ERR",
        error_message="Missing required field 'term_id'",
    )

    assert proposal.field_mappings == {"semester_id": "term_id"}
    assert proposal.confidence == 0.98
    assert proposal.injected_test_input is False
