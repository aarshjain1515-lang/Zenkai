"""Deterministic Safety Validator for Zenkai.
Enforces non-negotiable recovery-system invariants and rejects unsafe AI-proposed repairs.
"""

from typing import Any, Dict
from zenkai.models import PatchProposal, ValidationCheck, ValidationResult
from zenkai.services import AdvisorService, FinanceService


ALLOWED_FIELD_MAPPINGS = {
    "semester_id": "term_id",
    "course_id": "course_code",
    "learner_id": "student_id",
}

FORBIDDEN_PATCH_FIELDS = {
    "bypass_fee",
    "force_enroll",
    "override_advisor",
    "waive_prerequisite",
    "grade_override",
    "credit_bonus",
}


class DeterministicValidator:
    """Enforces zero-trust safety gates on AI-proposed patches."""

    def __init__(self, finance_service: FinanceService, advisor_service: AdvisorService):
        self.finance_service = finance_service
        self.advisor_service = advisor_service

    def validate(self, original_payload: Dict[str, Any], patch: PatchProposal) -> ValidationResult:
        checks = []
        patched = patch.patched_payload

        # 1. Identity Preservation Rule
        student_orig = original_payload.get("student_id")
        student_patched = patched.get("student_id")
        if student_orig != student_patched:
            checks.append(ValidationCheck(
                rule="IDENTITY_PRESERVATION",
                passed=False,
                details=f"Student ID mutated from '{student_orig}' to '{student_patched}'. Forbidden."
            ))
            return ValidationResult(is_valid=False, checks=checks, rejection_reason="Violation: Student ID identity cannot be altered.")
        checks.append(ValidationCheck(rule="IDENTITY_PRESERVATION", passed=True, details=f"Student ID verified: {student_orig}"))

        # 2. Course Code Preservation Rule
        course_orig = original_payload.get("course_code")
        course_patched = patched.get("course_code")
        if course_orig != course_patched:
            checks.append(ValidationCheck(
                rule="COURSE_INTEGRITY",
                passed=False,
                details=f"Course code mutated from '{course_orig}' to '{course_patched}'."
            ))
            return ValidationResult(is_valid=False, checks=checks, rejection_reason="Violation: Target course identity cannot be mutated.")
        checks.append(ValidationCheck(rule="COURSE_INTEGRITY", passed=True, details=f"Course target preserved: {course_orig}"))

        # 3. Value Preservation on Remapping
        for src_field, dst_field in patch.field_mappings.items():
            if src_field in original_payload:
                val_orig = original_payload[src_field]
                val_patched = patched.get(dst_field)
                if val_orig != val_patched:
                    checks.append(ValidationCheck(
                        rule="VALUE_PRESERVATION",
                        passed=False,
                        details=f"Value for mapped field changed: '{src_field}' ({val_orig}) != '{dst_field}' ({val_patched})"
                    ))
                    return ValidationResult(is_valid=False, checks=checks, rejection_reason="Violation: Mapped field value must remain identical.")
        checks.append(ValidationCheck(rule="VALUE_PRESERVATION", passed=True, details="Authoritative semantic values preserved."))

        # 4. Forbidden Injections & Privilege Escalation Check
        for key in patched.keys():
            if key in FORBIDDEN_PATCH_FIELDS:
                checks.append(ValidationCheck(
                    rule="PRIVILEGE_ESCALATION_CHECK",
                    passed=False,
                    details=f"Detected forbidden administrative bypass flag: '{key}'"
                ))
                return ValidationResult(is_valid=False, checks=checks, rejection_reason=f"Forbidden patch payload contains restricted key '{key}'.")
        checks.append(ValidationCheck(rule="PRIVILEGE_ESCALATION_CHECK", passed=True, details="No privilege escalation keys detected."))

        # 5. Finance Prerequisite Verification (Never invent payments)
        clearance_id = patched.get("clearance_id")
        if not clearance_id:
            checks.append(ValidationCheck(rule="FINANCE_PREREQUISITE", passed=False, details="Missing fee clearance reference."))
            return ValidationResult(is_valid=False, checks=checks, rejection_reason="Fee clearance prerequisite missing.")

        clearance_record = self.finance_service.verify_clearance(clearance_id)
        if not clearance_record or clearance_record.status != "CLEARED":
            checks.append(ValidationCheck(rule="FINANCE_PREREQUISITE", passed=False, details=f"Clearance ID '{clearance_id}' not found or unverified."))
            return ValidationResult(is_valid=False, checks=checks, rejection_reason="Fee clearance record cannot be verified in finance database.")
        checks.append(ValidationCheck(rule="FINANCE_PREREQUISITE", passed=True, details=f"Verified valid fee clearance {clearance_id} ({clearance_record.clearance_hash})"))

        # 6. Advisor Prerequisite Verification (Never invent approvals)
        approval_id = patched.get("approval_id")
        if not approval_id:
            checks.append(ValidationCheck(rule="ADVISOR_APPROVAL_PREREQUISITE", passed=False, details="Missing advisor approval reference."))
            return ValidationResult(is_valid=False, checks=checks, rejection_reason="Advisor approval prerequisite missing.")

        approval_record = self.advisor_service.verify_approval(approval_id)
        if not approval_record or approval_record.status != "APPROVED":
            checks.append(ValidationCheck(rule="ADVISOR_APPROVAL_PREREQUISITE", passed=False, details=f"Approval ID '{approval_id}' not found or unverified."))
            return ValidationResult(is_valid=False, checks=checks, rejection_reason="Advisor approval record cannot be verified in academic records.")
        checks.append(ValidationCheck(rule="ADVISOR_APPROVAL_PREREQUISITE", passed=True, details=f"Verified valid academic approval {approval_id} ({approval_record.signature})"))

        # 7. Safe Mappings Allowlist Check
        for src_field, dst_field in patch.field_mappings.items():
            if src_field not in ALLOWED_FIELD_MAPPINGS or ALLOWED_FIELD_MAPPINGS[src_field] != dst_field:
                checks.append(ValidationCheck(
                    rule="ALLOWED_FIELD_MAPPINGS",
                    passed=False,
                    details=f"Unrecognized field mapping: '{src_field}' -> '{dst_field}'. Not in allowlist."
                ))
                return ValidationResult(is_valid=False, checks=checks, rejection_reason="Field mapping is not within registered system ontology.")
        checks.append(ValidationCheck(rule="ALLOWED_FIELD_MAPPINGS", passed=True, details="Field mappings conform to registered connector schema."))

        return ValidationResult(is_valid=True, checks=checks)
