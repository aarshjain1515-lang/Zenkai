"""Unit and integration tests for Zenkai self-healing recovery layer.
Covers schema drift, timeout idempotency, unsafe patch rejection, and hot-patch caching.
"""

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
def engine():
    eng = ZenkaiEngine()
    eng.reset_state()
    return eng


def test_schema_drift_autonomous_recovery(engine: ZenkaiEngine):
    """Flagship Scenario: Schema drift (semester_id -> term_id) recovers automatically."""
    student = Student(student_id="STU-8821", name="Aarav Sharma", email="aarav@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    # Run workflow with schema drift failure
    wf = engine.create_registration_journey(student, course, term_id="SEM-2026-FALL", scenario=FailureScenario.SCHEMA_DRIFT)
    result = engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    # Assert workflow recovered and completed
    assert result.status == StepStatus.SUCCESS
    assert result.steps[StepName.FEE_CLEARANCE].status == StepStatus.SUCCESS
    assert result.steps[StepName.ADVISOR_APPROVAL].status == StepStatus.SUCCESS
    assert result.steps[StepName.REGISTRATION].status == StepStatus.RECOVERED
    assert result.steps[StepName.COURSE_ACCESS].status == StepStatus.SUCCESS

    # Assert incident evidence and patch verification
    assert result.incident_id is not None
    incident = engine.incidents[result.incident_id]
    assert incident.status == IncidentStatus.RECOVERED
    assert incident.patch_proposal is not None
    assert incident.patch_proposal.field_mappings == {"semester_id": "term_id"}
    assert incident.validation_result.is_valid is True

    # Assert destination record verified
    dest_reg = engine.erp.get_registration("STU-8821", "CS-499")
    assert dest_reg is not None
    assert dest_reg.term_id == "SEM-2026-FALL"

    # Assert LMS learning access provisioned
    dest_lms = engine.lms.check_access("STU-8821", "CS-499")
    assert dest_lms is not None
    assert "canvas.zenkai-campus.edu" in dest_lms.lms_url


def test_unsafe_proposal_is_blocked_by_deterministic_validator(engine: ZenkaiEngine):
    """Safety Invariant Test: Rogue AI patch trying to mutate student ID or bypass fee is blocked."""
    student = Student(student_id="STU-9999", name="Test Student", email="test@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = engine.create_registration_journey(student, course, term_id="SEM-2026-FALL", scenario=FailureScenario.UNSAFE_PROPOSAL)
    result = engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    # Assert workflow halted and failed safely
    assert result.status == StepStatus.FAILED
    assert result.steps[StepName.REGISTRATION].status == StepStatus.FAILED
    assert result.steps[StepName.COURSE_ACCESS].status == StepStatus.PENDING

    # Assert incident was BLOCKED by validator
    assert result.incident_id is not None
    incident = engine.incidents[result.incident_id]
    assert incident.status == IncidentStatus.BLOCKED
    assert incident.validation_result.is_valid is False
    assert incident.validation_result.rejection_reason is not None

    # Assert destination write was NOT executed
    dest_reg = engine.erp.get_registration("STU-9999", "CS-499")
    assert dest_reg is None


def test_timeout_and_idempotency_recovery(engine: ZenkaiEngine):
    """Timeout Scenario: Preserves idempotency key and prevents duplicate enrollment."""
    student = Student(student_id="STU-5544", name="Priya Patel", email="priya@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = engine.create_registration_journey(student, course, term_id="SEM-2026-FALL", scenario=FailureScenario.TIMEOUT)
    result = engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

    assert result.status == StepStatus.SUCCESS
    assert result.steps[StepName.REGISTRATION].status == StepStatus.RECOVERED
    assert result.steps[StepName.COURSE_ACCESS].status == StepStatus.SUCCESS

    # Verify that only 1 registration record exists in ERP (no duplicates)
    regs = [r for r in engine.erp.registrations.values() if r.student_id == "STU-5544"]
    assert len(regs) == 1


def test_human_approval_gate_lifecycle(engine: ZenkaiEngine):
    """Human-in-the-loop: Workflow halts at PENDING_APPROVAL until explicit human approval."""
    student = Student(student_id="STU-3321", name="Rohan Verma", email="rohan@campus.edu")
    course = Course(course_code="CS-499", title="Senior Capstone Project")

    wf = engine.create_registration_journey(student, course, term_id="SEM-2026-FALL", scenario=FailureScenario.SCHEMA_DRIFT)
    result = engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=False)

    # Workflow should hold at failed step awaiting approval
    assert result.status == StepStatus.FAILED
    incident = engine.incidents[result.incident_id]
    assert incident.status == IncidentStatus.PENDING_APPROVAL

    # Admin reviews and approves the patch with proposal_hash
    resumed_wf = engine.approve_incident_and_resume(incident.incident_id, proposal_hash=incident.proposal_hash)
    assert resumed_wf.status == StepStatus.SUCCESS
    recovered_inc = engine.get_incident(incident.incident_id)
    assert recovered_inc.status == IncidentStatus.RECOVERED
    assert engine.erp.get_registration("STU-3321", "CS-499") is not None


def test_hot_patch_caching_protects_subsequent_students(engine: ZenkaiEngine):
    """Hot-Patching: Once a schema drift is healed, the next student experiences zero failure."""
    # Student 1 triggers the initial incident and heals it
    s1 = Student(student_id="STU-1001", name="Student One", email="s1@campus.edu")
    c = Course(course_code="CS-499", title="Senior Capstone Project")
    wf1 = engine.create_registration_journey(s1, c, term_id="SEM-2026-FALL", scenario=FailureScenario.SCHEMA_DRIFT)
    engine.execute_journey(wf1.workflow_id, auto_approve_safe_patches=True)

    assert "semester_id" in engine.active_adapters

    # Student 2 submits registration with legacy schema, but adapter applies automatically!
    s2 = Student(student_id="STU-1002", name="Student Two", email="s2@campus.edu")
    wf2 = engine.create_registration_journey(s2, c, term_id="SEM-2026-FALL", scenario=FailureScenario.NONE)
    res2 = engine.execute_journey(wf2.workflow_id, auto_approve_safe_patches=True)

    assert res2.status == StepStatus.SUCCESS
    assert res2.steps[StepName.REGISTRATION].status == StepStatus.SUCCESS
    assert engine.metrics["hot_patches_applied"] >= 1
