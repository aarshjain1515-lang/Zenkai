"""Core domain models and schema definitions for Zenkai self-healing recovery layer."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class StepName(str, Enum):
    FEE_CLEARANCE = "FEE_CLEARANCE"
    ADVISOR_APPROVAL = "ADVISOR_APPROVAL"
    REGISTRATION = "REGISTRATION"
    COURSE_ACCESS = "COURSE_ACCESS"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    ADAPTER_DISABLED = "ADAPTER_DISABLED"


class IncidentStatus(str, Enum):
    DETECTED = "DETECTED"
    DIAGNOSING = "DIAGNOSING"
    PROPOSED = "PROPOSED"
    VALIDATING = "VALIDATING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REPLAYING = "REPLAYING"
    RECOVERED = "RECOVERED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    ADAPTER_DISABLED = "ADAPTER_DISABLED"


class FailureScenario(str, Enum):
    NONE = "NONE"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    TIMEOUT = "TIMEOUT"
    UNSAFE_PROPOSAL = "UNSAFE_PROPOSAL"
    BROKEN_HANDOFF = "BROKEN_HANDOFF"


class PatchType(str, Enum):
    FIELD_MAPPING = "FIELD_MAPPING"
    RETRY_WITH_RECONCILIATION = "RETRY_WITH_RECONCILIATION"
    ROUTE_REPLACEMENT = "ROUTE_REPLACEMENT"
    UNSAFE_MODIFICATION = "UNSAFE_MODIFICATION"


class Student(BaseModel):
    student_id: str
    name: str
    email: str
    program: str = "Computer Science"


class Course(BaseModel):
    course_code: str
    title: str
    credits: int = 4
    capacity: int = 40
    enrolled_count: int = 0


class FeeClearance(BaseModel):
    clearance_id: str
    student_id: str
    term_id: str
    amount_paid: float
    status: str = "CLEARED"
    clearance_hash: str


class AdvisorApproval(BaseModel):
    approval_id: str
    student_id: str
    course_code: str
    term_id: str
    advisor_id: str
    status: str = "APPROVED"
    signature: str


class RegistrationRecord(BaseModel):
    registration_id: str
    student_id: str
    course_code: str
    term_id: str
    idempotency_key: str
    status: str = "CONFIRMED"
    created_at: str


class CourseAccessRecord(BaseModel):
    access_id: str
    student_id: str
    course_code: str
    lms_url: str
    status: str = "PROVISIONED"
    created_at: str


class ValidationCheck(BaseModel):
    rule: str
    passed: bool
    details: str


class ValidationResult(BaseModel):
    is_valid: bool
    checks: List[ValidationCheck] = Field(default_factory=list)
    rejection_reason: Optional[str] = None


class PatchProposal(BaseModel):
    patch_id: str
    incident_id: str
    root_cause: str
    evidence_citations: List[str] = Field(default_factory=list)
    patch_type: PatchType
    field_mappings: Dict[str, str] = Field(default_factory=dict)
    original_payload: Dict[str, Any]
    patched_payload: Dict[str, Any]
    requires_human_approval: bool = True
    confidence: float = 0.95
    schema_version: str = "v2.4"
    proposal_hash: Optional[str] = None
    injected_test_input: bool = False


class StepExecution(BaseModel):
    step_name: StepName
    status: StepStatus = StepStatus.PENDING
    details: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class IncidentRecord(BaseModel):
    incident_id: str
    workflow_id: str
    failed_step: StepName
    error_code: str
    error_message: str
    raw_payload: Dict[str, Any]
    status: IncidentStatus = IncidentStatus.DETECTED
    proposal_hash: Optional[str] = None
    patch_proposal: Optional[PatchProposal] = None
    validation_result: Optional[ValidationResult] = None
    recovery_duration_ms: Optional[float] = None
    recovery_seconds: Optional[float] = None
    retry_count: int = 0
    max_retries: int = 3
    schema_version: str = "v2.4"
    injected_test_input: bool = False
    created_at: str
    recovered_at: Optional[str] = None


class WorkflowRecord(BaseModel):
    workflow_id: str
    student: Student
    course: Course
    term_id: str
    status: StepStatus = StepStatus.PENDING
    steps: Dict[str, StepExecution] = Field(default_factory=dict)
    active_scenario: FailureScenario = FailureScenario.NONE
    incident_id: Optional[str] = None
    created_at: str
    updated_at: str
