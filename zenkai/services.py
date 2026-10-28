"""Simulated university services: Finance, Advisor, Registration (ERP), and LMS.
Includes failure injection controls for schema drift, timeout, and duplicate prevention.
"""

import hashlib
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from zenkai.models import (
    AdvisorApproval,
    Course,
    CourseAccessRecord,
    FeeClearance,
    RegistrationRecord,
    Student,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FinanceService:
    """University Finance / Student Accounts Service."""

    def __init__(self):
        self._clearances: Dict[str, FeeClearance] = {}

    def issue_clearance(self, student: Student, term_id: str, amount: float = 12500.0) -> FeeClearance:
        digest = hashlib.sha256(f"{student.student_id}:{term_id}:{amount}:AUTHORISED".encode()).hexdigest()[:16]
        clearance = FeeClearance(
            clearance_id=f"FEE-{student.student_id}-{term_id}",
            student_id=student.student_id,
            term_id=term_id,
            amount_paid=amount,
            status="CLEARED",
            clearance_hash=f"sha256_{digest}",
        )
        self._clearances[clearance.clearance_id] = clearance
        return clearance

    def verify_clearance(self, clearance_id: str) -> Optional[FeeClearance]:
        return self._clearances.get(clearance_id)


class AdvisorService:
    """Academic Advising and Departmental Approvals."""

    def __init__(self):
        self._approvals: Dict[str, AdvisorApproval] = {}

    def grant_approval(self, student: Student, course: Course, term_id: str, advisor_id: str = "FAC-DR-MENON") -> AdvisorApproval:
        sig = hashlib.sha256(f"{student.student_id}:{course.course_code}:{advisor_id}:VERIFIED".encode()).hexdigest()[:16]
        approval = AdvisorApproval(
            approval_id=f"ADV-{student.student_id}-{course.course_code}",
            student_id=student.student_id,
            course_code=course.course_code,
            term_id=term_id,
            advisor_id=advisor_id,
            status="APPROVED",
            signature=f"sig_{sig}",
        )
        self._approvals[approval.approval_id] = approval
        return approval

    def verify_approval(self, approval_id: str) -> Optional[AdvisorApproval]:
        return self._approvals.get(approval_id)


class RegistrationERPService:
    """Central University Registration ERP (Destination Service).
    Contains strict schema requirements and failure injection switches.
    """

    def __init__(self):
        self.enforce_schema_v2 = True  # Schema drift: expects 'term_id', rejects 'semester_id'
        self.simulate_timeout = False
        self.timeout_write_succeeded = False  # To test idempotency reconciliation
        self.registrations: Dict[str, RegistrationRecord] = {}
        self.idempotency_table: Dict[str, RegistrationRecord] = {}

    def register(self, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Processes a registration request."""
        # Check idempotency first (Recovery invariant: safe retries)
        idempotency_key = payload.get("idempotency_key")
        if idempotency_key and idempotency_key in self.idempotency_table:
            existing = self.idempotency_table[idempotency_key]
            return 200, {
                "status": "EXISTING_REGISTRATION",
                "message": "Idempotent hit: Registration already recorded",
                "record": existing.model_dump(),
            }

        # Check Timeout Simulation
        if self.simulate_timeout:
            if self.timeout_write_succeeded:
                # The write actually happened inside ERP, but the network connection dropped
                reg_id = f"REG-{payload.get('student_id')}-{payload.get('course_code')}"
                record = RegistrationRecord(
                    registration_id=reg_id,
                    student_id=payload.get("student_id", "UNKNOWN"),
                    course_code=payload.get("course_code", "UNKNOWN"),
                    term_id=payload.get("term_id", payload.get("semester_id", "TERM-UNKNOWN")),
                    idempotency_key=idempotency_key or "N/A",
                    status="CONFIRMED",
                    created_at=now_iso(),
                )
                self.registrations[reg_id] = record
                if idempotency_key:
                    self.idempotency_table[idempotency_key] = record
            return 504, {
                "error": "GATEWAY_TIMEOUT",
                "message": "Downstream ERP socket closed without response acknowledgment",
            }

        # Check Schema Version Contract
        if self.enforce_schema_v2:
            if "term_id" not in payload:
                if "semester_id" in payload:
                    return 422, {
                        "error": "SCHEMA_MISMATCH",
                        "code": "CONTRACT_DRIFT_ERR",
                        "message": "Missing required field 'term_id'. Deprecated field 'semester_id' was received. Schema contract v2.4 requires 'term_id'.",
                        "received_keys": list(payload.keys()),
                        "expected_keys": ["student_id", "course_code", "term_id", "clearance_id", "approval_id", "idempotency_key"],
                    }
                return 400, {
                    "error": "BAD_REQUEST",
                    "code": "MISSING_TERM",
                    "message": "Field 'term_id' is mandatory.",
                }

        # Normal successful write
        student_id = payload.get("student_id")
        course_code = payload.get("course_code")
        term_id = payload.get("term_id")

        if not student_id or not course_code or not term_id:
            return 400, {"error": "INVALID_PAYLOAD", "message": "Missing required enrollment fields."}

        reg_id = f"REG-{student_id}-{course_code}"
        record = RegistrationRecord(
            registration_id=reg_id,
            student_id=student_id,
            course_code=course_code,
            term_id=term_id,
            idempotency_key=idempotency_key or f"IDEMP-{student_id}-{course_code}",
            status="CONFIRMED",
            created_at=now_iso(),
        )
        self.registrations[reg_id] = record
        if idempotency_key:
            self.idempotency_table[idempotency_key] = record

        return 200, {
            "status": "CONFIRMED",
            "message": "Student successfully registered in course",
            "record": record.model_dump(),
        }

    def get_registration(self, student_id: str, course_code: str) -> Optional[RegistrationRecord]:
        return self.registrations.get(f"REG-{student_id}-{course_code}")


class LearningPortalService:
    """University Learning Management System (LMS / Canvas / Moodle)."""

    def __init__(self, registration_service: RegistrationERPService):
        self.reg_service = registration_service
        self.access_records: Dict[str, CourseAccessRecord] = {}

    def provision_access(self, student_id: str, course_code: str) -> Tuple[int, Dict[str, Any]]:
        reg = self.reg_service.get_registration(student_id, course_code)
        if not reg or reg.status != "CONFIRMED":
            return 403, {
                "error": "REGISTRATION_REQUIRED",
                "message": f"Student {student_id} does not have a confirmed ERP registration for {course_code}.",
            }

        access_id = f"LMS-{student_id}-{course_code}"
        access = CourseAccessRecord(
            access_id=access_id,
            student_id=student_id,
            course_code=course_code,
            lms_url=f"https://canvas.zenkai-campus.edu/courses/{course_code}",
            status="PROVISIONED",
            created_at=now_iso(),
        )
        self.access_records[access_id] = access
        return 200, {
            "status": "PROVISIONED",
            "message": "Course learning materials and workspace unlocked.",
            "record": access.model_dump(),
        }

    def check_access(self, student_id: str, course_code: str) -> Optional[CourseAccessRecord]:
        return self.access_records.get(f"LMS-{student_id}-{course_code}")
