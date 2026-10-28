"""Tests for Phase 1: SQLite persistence, approval hash binding,
verified recovery with rollback, attempt bounds, and measured recovery timing.
"""

import os
import tempfile
import pytest
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


def test_restart_persistence(temp_engine: ZenkaiEngine):
    """Data must survive an engine/server restart by reading from SQLite."""
    student = Student(student_id="STU-7701", name="Persistent Student", email="p@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    # Execute workflow and trigger self-healing
    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.SCHEMA_DRIFT)
    res_wf = temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    db_path = temp_engine.db.db_path

    # Simulate restart by creating a completely new engine instance pointing to the same db
    restarted_engine = ZenkaiEngine(db_path=db_path)

    # Assert workflow survived restart
    recovered_wf = restarted_engine.get_workflow(res_wf.workflow_id)
    assert recovered_wf is not None
    assert recovered_wf.student.student_id == "STU-7701"
    assert recovered_wf.status == StepStatus.SUCCESS

    # Assert incident survived restart
    inc = restarted_engine.get_incident(recovered_wf.incident_id)
    assert inc is not None
    assert inc.status == IncidentStatus.RECOVERED
    assert inc.proposal_hash is not None
    assert inc.recovery_seconds is not None
    assert inc.recovery_seconds > 0.0

    # Assert hot-patch adapter survived restart
    assert "semester_id" in restarted_engine.active_adapters
    assert restarted_engine.active_adapters["semester_id"] == "term_id"


def test_tampered_and_stale_hash_rejected(temp_engine: ZenkaiEngine):
    """Approval requires exact proposal_hash; mismatched or stale hashes are rejected."""
    student = Student(student_id="STU-7702", name="Hash Student", email="h@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.SCHEMA_DRIFT)
    res_wf = temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=False)

    inc = temp_engine.get_incident(res_wf.incident_id)
    assert inc is not None
    assert inc.status == IncidentStatus.PENDING_APPROVAL
    real_hash = inc.proposal_hash
    assert real_hash is not None

    # 1. Attempt with tampered / wrong hash -> Rejected
    with pytest.raises(ValueError, match="Mismatched or tampered proposal hash"):
        temp_engine.approve_incident_and_resume(inc.incident_id, proposal_hash="tampered_fake_hash_12345")

    # 2. Approve with correct hash -> Succeeds
    resumed_wf = temp_engine.approve_incident_and_resume(inc.incident_id, proposal_hash=real_hash)
    assert resumed_wf.status == StepStatus.SUCCESS

    # 3. Attempt with now-stale hash (status is no longer PENDING_APPROVAL) -> Rejected
    with pytest.raises(ValueError, match="cannot approve"):
        temp_engine.approve_incident_and_resume(inc.incident_id, proposal_hash=real_hash)


def test_double_approve_single_execution(temp_engine: ZenkaiEngine):
    """Double-approving an incident must execute exactly once and reject the duplicate."""
    student = Student(student_id="STU-7703", name="Double Student", email="d@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.SCHEMA_DRIFT)
    res_wf = temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=False)

    inc = temp_engine.get_incident(res_wf.incident_id)
    assert inc is not None
    real_hash = inc.proposal_hash

    # First approval
    res1 = temp_engine.approve_incident_and_resume(inc.incident_id, proposal_hash=real_hash)
    assert res1.status == StepStatus.SUCCESS

    # Immediate second approval attempt with the same hash
    with pytest.raises(ValueError):
        temp_engine.approve_incident_and_resume(inc.incident_id, proposal_hash=real_hash)

    # Destination records should have exactly one registration
    regs = [r for r in temp_engine.erp.registrations.values() if r.student_id == "STU-7703"]
    assert len(regs) == 1


def test_forced_verification_failure_yields_rollback_never_recovered(temp_engine: ZenkaiEngine):
    """If destination verification fails, adapters are rolled back and status is never RECOVERED."""
    student = Student(student_id="STU-7704", name="Rollback Student", email="r@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    # Force verification failure flag
    temp_engine.force_verification_failure = True

    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.SCHEMA_DRIFT)
    res_wf = temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    inc = temp_engine.get_incident(res_wf.incident_id)
    assert inc is not None

    # Status must be ROLLED_BACK or VERIFICATION_FAILED, NEVER RECOVERED
    assert inc.status in (IncidentStatus.ROLLED_BACK, IncidentStatus.VERIFICATION_FAILED)
    assert inc.status != IncidentStatus.RECOVERED
    assert res_wf.status == StepStatus.ROLLED_BACK

    # Adapter snapshot was restored (adapter deleted / rolled back)
    assert "semester_id" not in temp_engine.active_adapters


def test_attempt_bound_honoured_leads_to_needs_human(temp_engine: ZenkaiEngine):
    """Exhausting retry attempts transitions the incident to NEEDS_HUMAN."""
    student = Student(student_id="STU-7705", name="Bound Student", email="b@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.SCHEMA_DRIFT)

    # Intercept and set max retries reached
    temp_engine.force_verification_failure = True
    res_wf = temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    inc = temp_engine.get_incident(res_wf.incident_id)
    assert inc is not None
    inc.retry_count = 3
    inc.max_retries = 3
    temp_engine.db.save_incident(inc.model_dump())

    # Triggering recovery again exceeds bound
    result_inc = temp_engine._execute_verified_recovery(res_wf, inc, rec_start=0.0)
    assert result_inc.status == IncidentStatus.NEEDS_HUMAN


def test_recovery_seconds_persisted(temp_engine: ZenkaiEngine):
    """Measured recovery time in seconds is persisted to SQLite and retrievable."""
    student = Student(student_id="STU-7706", name="Timed Student", email="t@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = temp_engine.create_registration_journey(student, course, scenario=FailureScenario.SCHEMA_DRIFT)
    res_wf = temp_engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    inc = temp_engine.get_incident(res_wf.incident_id)
    assert inc is not None
    assert inc.recovery_seconds is not None
    assert isinstance(inc.recovery_seconds, float)
    assert inc.recovery_seconds >= 0.0


def test_sustained_check_flow(temp_engine: ZenkaiEngine):
    """Sustained check measures success rates across before/during/after phases."""
    stats = temp_engine.run_sustained_check(count_per_phase=3)
    assert stats["before_success"] == 3
    assert stats["during_success"] == 0
    assert stats["after_success"] == 3
    assert stats["recovery_seconds"] >= 0.0
