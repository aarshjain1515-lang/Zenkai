"""Recovery coordinator and workflow execution engine for Zenkai.
Includes durable SQLite persistence, cryptographic approval binding,
verified recovery with atomic rollback, and measured recovery timing.
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from zenkai.db import ZenkaiDatabase, canonical_json
from zenkai.diagnostics import DiagnosticAgent, compute_proposal_hash
from zenkai.models import (
    Course,
    FailureScenario,
    IncidentRecord,
    IncidentStatus,
    PatchProposal,
    StepExecution,
    StepName,
    StepStatus,
    Student,
    ValidationResult,
    WorkflowRecord,
)
from zenkai.services import (
    AdvisorService,
    FinanceService,
    LearningPortalService,
    RegistrationERPService,
    now_iso,
)
from zenkai.validator import DeterministicValidator


class ZenkaiEngine:
    """End-to-end recovery engine orchestrating multi-system student workflows."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db = ZenkaiDatabase(db_path)
        else:
            self.db = ZenkaiDatabase()

        self.finance = FinanceService()
        self.advisor = AdvisorService()
        self.erp = RegistrationERPService()
        self.lms = LearningPortalService(self.erp)

        self.diagnostics = DiagnosticAgent()
        self.validator = DeterministicValidator(self.finance, self.advisor)

        # Load persisted adapters
        self.active_adapters: Dict[str, str] = self.db.list_adapters()

        # In-memory fast cache mirroring DB
        self.workflows: Dict[str, WorkflowRecord] = {}
        self.incidents: Dict[str, IncidentRecord] = {}

        # Testing hooks
        self.force_verification_failure = False

        # System Telemetry & Metrics
        self.metrics = {
            "total_workflows": 0,
            "completed_workflows": 0,
            "failed_workflows": 0,
            "healed_workflows": 0,
            "invariants_enforced": 0,
            "hot_patches_applied": 0,
            "avg_recovery_seconds": 0.0,
        }

    def log_event(self, service: str, event_type: str, details: Dict[str, Any], workflow_id: Optional[str] = None, incident_id: Optional[str] = None, reason: Optional[str] = None):
        """Structured observability event written to durable SQLite."""
        ts = now_iso()
        entry = {
            "timestamp": ts,
            "service": service,
            "event_type": event_type,
            "workflow_id": workflow_id or "N/A",
            "incident_id": incident_id or "N/A",
            "reason": reason or "",
            "details": details,
        }
        self.db.record_timeline_event(
            service=service,
            event_type=event_type,
            details=details,
            timestamp=ts,
            workflow_id=workflow_id,
            incident_id=incident_id,
            reason=reason,
        )
        return entry

    def create_registration_journey(
        self,
        student: Student,
        course: Course,
        term_id: str = "SEM-2026-FALL",
        scenario: FailureScenario = FailureScenario.NONE,
    ) -> WorkflowRecord:
        """Initializes and persists a student registration workflow."""
        workflow_id = f"WF-{uuid.uuid4().hex[:8].upper()}"
        workflow = WorkflowRecord(
            workflow_id=workflow_id,
            student=student,
            course=course,
            term_id=term_id,
            status=StepStatus.PENDING,
            steps={
                StepName.FEE_CLEARANCE: StepExecution(step_name=StepName.FEE_CLEARANCE),
                StepName.ADVISOR_APPROVAL: StepExecution(step_name=StepName.ADVISOR_APPROVAL),
                StepName.REGISTRATION: StepExecution(step_name=StepName.REGISTRATION),
                StepName.COURSE_ACCESS: StepExecution(step_name=StepName.COURSE_ACCESS),
            },
            active_scenario=scenario,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        self.workflows[workflow_id] = workflow
        self.db.save_workflow(workflow.model_dump())
        self.metrics["total_workflows"] += 1

        self.log_event("zenkai_orchestrator", "WORKFLOW_INITIATED", {
            "student_id": student.student_id,
            "course_code": course.course_code,
            "term_id": term_id,
            "scenario": scenario.value,
        }, workflow_id=workflow_id, reason="New student registration request")

        return workflow

    def execute_journey(self, workflow_id: str, auto_approve_safe_patches: bool = True) -> WorkflowRecord:
        """Executes the complete multi-step journey with autonomous failure recovery."""
        workflow = self.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow.status = StepStatus.RUNNING
        workflow.updated_at = now_iso()
        self.db.save_workflow(workflow.model_dump())

        # Step 1: Fee Clearance Verification
        s1 = workflow.steps[StepName.FEE_CLEARANCE]
        s1.status = StepStatus.RUNNING
        s1.started_at = now_iso()
        clearance = self.finance.issue_clearance(workflow.student, workflow.term_id)
        s1.status = StepStatus.SUCCESS
        s1.finished_at = now_iso()
        s1.details = f"Verified: {clearance.clearance_id} ({clearance.clearance_hash})"
        self.log_event("finance_service", "FEE_VERIFIED", {"clearance_id": clearance.clearance_id}, workflow_id=workflow_id)

        # Step 2: Faculty Advisor Approval
        s2 = workflow.steps[StepName.ADVISOR_APPROVAL]
        s2.status = StepStatus.RUNNING
        s2.started_at = now_iso()
        approval = self.advisor.grant_approval(workflow.student, workflow.course, workflow.term_id)
        s2.status = StepStatus.SUCCESS
        s2.finished_at = now_iso()
        s2.details = f"Approved by {approval.advisor_id} ({approval.signature})"
        self.log_event("advisor_service", "APPROVAL_CONFIRMED", {"approval_id": approval.approval_id}, workflow_id=workflow_id)

        # Step 3: Central Registration ERP
        s3 = workflow.steps[StepName.REGISTRATION]
        s3.status = StepStatus.RUNNING
        s3.started_at = now_iso()

        # Prepare original payload (legacy portal uses 'semester_id')
        reg_payload = {
            "student_id": workflow.student.student_id,
            "course_code": workflow.course.course_code,
            "semester_id": workflow.term_id,
            "clearance_id": clearance.clearance_id,
            "approval_id": approval.approval_id,
            "idempotency_key": f"IDEMP-{workflow.workflow_id}",
        }

        # Check if an active hot-patch adapter applies
        effective_payload = dict(reg_payload)
        self.active_adapters = self.db.list_adapters()
        if self.active_adapters:
            for src_field, dst_field in self.active_adapters.items():
                if src_field in effective_payload:
                    effective_payload[dst_field] = effective_payload.pop(src_field)
                    self.metrics["hot_patches_applied"] += 1
                    self.log_event("zenkai_gateway", "HOT_PATCH_ADAPTER_APPLIED", {
                        "mapped": f"{src_field} -> {dst_field}"
                    }, workflow_id=workflow_id, reason="Runtime adapter translation")

        # Configure ERP failure controls based on active scenario
        if workflow.active_scenario == FailureScenario.TIMEOUT:
            self.erp.simulate_timeout = True
            self.erp.timeout_write_succeeded = True
        else:
            self.erp.simulate_timeout = False

        status_code, response_data = self.erp.register(effective_payload)

        if status_code != 200:
            # FAILURE TRAPPED BY ZENKAI OBSERVER
            s3.status = StepStatus.FAILED
            s3.details = f"ERP Error {status_code}: {response_data.get('message', 'Registration rejected')}"

            # Initiate Recovery
            incident = self._handle_failure(
                workflow=workflow,
                failed_step=StepName.REGISTRATION,
                status_code=status_code,
                response_data=response_data,
                raw_payload=reg_payload,
                auto_approve=auto_approve_safe_patches,
            )

            if incident.status == IncidentStatus.RECOVERED:
                s3.status = StepStatus.RECOVERED
                s3.details = "Recovered via autonomous patch and verified in ERP registry"
                self.metrics["healed_workflows"] += 1
            elif incident.status == IncidentStatus.BLOCKED:
                workflow.status = StepStatus.FAILED
                self.metrics["failed_workflows"] += 1
                self.db.save_workflow(workflow.model_dump())
                return workflow
            elif incident.status == IncidentStatus.PENDING_APPROVAL:
                workflow.status = StepStatus.FAILED
                self.db.save_workflow(workflow.model_dump())
                return workflow
            elif incident.status == IncidentStatus.NEEDS_HUMAN:
                workflow.status = StepStatus.NEEDS_HUMAN
                self.metrics["failed_workflows"] += 1
                self.db.save_workflow(workflow.model_dump())
                return workflow
            elif incident.status in (IncidentStatus.VERIFICATION_FAILED, IncidentStatus.ROLLED_BACK):
                workflow.status = StepStatus.ROLLED_BACK
                self.metrics["failed_workflows"] += 1
                self.db.save_workflow(workflow.model_dump())
                return workflow
            else:
                workflow.status = StepStatus.FAILED
                self.metrics["failed_workflows"] += 1
                self.db.save_workflow(workflow.model_dump())
                return workflow
        else:
            s3.status = StepStatus.SUCCESS
            s3.details = "Enrolled directly in central ERP"

        # Step 4: LMS Course Access Provisioning
        s4 = workflow.steps[StepName.COURSE_ACCESS]
        s4.status = StepStatus.RUNNING
        s4.started_at = now_iso()

        lms_code, lms_data = self.lms.provision_access(workflow.student.student_id, workflow.course.course_code)
        if lms_code == 200:
            s4.status = StepStatus.SUCCESS
            s4.finished_at = now_iso()
            s4.details = f"LMS provisioned: {lms_data['record']['lms_url']}"
            workflow.status = StepStatus.SUCCESS
            self.metrics["completed_workflows"] += 1
            self.log_event("learning_portal", "ACCESS_PROVISIONED", lms_data["record"], workflow_id=workflow_id)
        else:
            s4.status = StepStatus.FAILED
            s4.details = lms_data.get("message", "Access denied")
            workflow.status = StepStatus.FAILED
            self.metrics["failed_workflows"] += 1

        workflow.updated_at = now_iso()
        self.db.save_workflow(workflow.model_dump())
        return workflow

    def _handle_failure(
        self,
        workflow: WorkflowRecord,
        failed_step: StepName,
        status_code: int,
        response_data: Dict[str, Any],
        raw_payload: Dict[str, Any],
        auto_approve: bool = True,
    ) -> IncidentRecord:
        """Autonomous error diagnosis, patch synthesis, validation, and idempotent replay."""
        rec_start = time.time()
        incident_id = f"INC-{uuid.uuid4().hex[:8].upper()}"
        workflow.incident_id = incident_id

        incident = IncidentRecord(
            incident_id=incident_id,
            workflow_id=workflow.workflow_id,
            failed_step=failed_step,
            error_code=str(response_data.get("code", f"HTTP_{status_code}")),
            error_message=str(response_data.get("message", "Unknown error")),
            raw_payload=raw_payload,
            status=IncidentStatus.DETECTED,
            created_at=now_iso(),
        )
        self.incidents[incident_id] = incident
        self.db.save_incident(incident.model_dump())

        self.log_event("zenkai_observer", "INCIDENT_DETECTED", {
            "error_code": incident.error_code,
            "error_message": incident.error_message,
            "failed_step": failed_step.value,
        }, workflow_id=workflow.workflow_id, incident_id=incident_id, reason="Downstream service failure")

        # 1. AI Diagnostic Agent
        incident.status = IncidentStatus.DIAGNOSING
        if workflow.active_scenario == FailureScenario.UNSAFE_PROPOSAL:
            proposal = self.diagnostics.generate_unsafe_patch(incident_id, raw_payload)
        else:
            proposal = self.diagnostics.diagnose_and_propose(
                incident_id=incident_id,
                failed_payload=raw_payload,
                error_code=incident.error_code,
                error_message=incident.error_message,
                error_details=response_data,
            )

        incident.patch_proposal = proposal
        incident.proposal_hash = proposal.proposal_hash
        incident.status = IncidentStatus.PROPOSED
        self.db.save_incident(incident.model_dump())

        self.log_event("zenkai_diagnostics", "PATCH_PROPOSED", {
            "patch_id": proposal.patch_id,
            "root_cause": proposal.root_cause,
            "patch_type": proposal.patch_type.value,
            "proposal_hash": proposal.proposal_hash,
        }, workflow_id=workflow.workflow_id, incident_id=incident_id, reason="AI diagnosis completed")

        # 2. Deterministic Safety Validator
        incident.status = IncidentStatus.VALIDATING
        validation = self.validator.validate(raw_payload, proposal)
        incident.validation_result = validation
        self.metrics["invariants_enforced"] += len(validation.checks)

        if not validation.is_valid:
            incident.status = IncidentStatus.BLOCKED
            self.db.save_incident(incident.model_dump())
            self.log_event("zenkai_validator", "SAFETY_VIOLATION_BLOCKED", {
                "rejection_reason": validation.rejection_reason,
                "checks": [c.model_dump() for c in validation.checks if not c.passed],
            }, workflow_id=workflow.workflow_id, incident_id=incident_id, reason="Invariant violation")
            return incident

        self.log_event("zenkai_validator", "VALIDATION_PASSED", {
            "checks_passed": len(validation.checks)
        }, workflow_id=workflow.workflow_id, incident_id=incident_id, reason="Zero-trust safety checks passed")

        # 3. Human Approval Gate or Policy Auto-Approval
        if proposal.requires_human_approval and not auto_approve:
            incident.status = IncidentStatus.PENDING_APPROVAL
            self.db.save_incident(incident.model_dump())
            return incident

        # If auto-approved, record approval
        app_id = f"APP-{uuid.uuid4().hex[:8].upper()}"
        self.db.record_approval(app_id, incident_id, proposal.proposal_hash, "policy_auto_approver", now_iso())
        self.db.mark_approval_executed(app_id)

        # 4. Verified Replay & Outcome Verification
        return self._execute_verified_recovery(workflow, incident, rec_start)

    def _execute_verified_recovery(
        self,
        workflow: WorkflowRecord,
        incident: IncidentRecord,
        rec_start: float,
    ) -> IncidentRecord:
        """Executes patch replay with retry bounds, business outcome verification, and atomic rollback."""
        proposal = incident.patch_proposal
        if not proposal:
            incident.status = IncidentStatus.FAILED
            self.db.save_incident(incident.model_dump())
            return incident

        # Check attempt bound
        if incident.retry_count >= incident.max_retries:
            incident.status = IncidentStatus.NEEDS_HUMAN
            self.db.save_incident(incident.model_dump())
            self.log_event("zenkai_recovery", "MAX_RETRIES_EXCEEDED", {
                "retry_count": incident.retry_count,
                "max_retries": incident.max_retries
            }, workflow_id=workflow.workflow_id, incident_id=incident.incident_id, reason="Exhausted bounded retry budget")
            return incident

        incident.retry_count += 1
        incident.status = IncidentStatus.REPLAYING

        # Snapshot active adapters before applying fix
        adapter_snapshot = self.db.list_adapters()

        # Apply adapter mapping if present
        if proposal.field_mappings:
            for src_field, dst_field in proposal.field_mappings.items():
                self.db.save_adapter(src_field, dst_field, proposal.schema_version, now_iso())
            self.active_adapters = self.db.list_adapters()

        # Turn off timeout simulation if recovering from timeout
        if workflow.active_scenario == FailureScenario.TIMEOUT:
            self.erp.simulate_timeout = False

        # Replay the ORIGINAL registration through the healed adapter
        replay_code, replay_data = self.erp.register(proposal.patched_payload)

        # Verify Business Result (Exact destination record exists, exactly once, no duplicates)
        dest_record = self.erp.get_registration(workflow.student.student_id, workflow.course.course_code)

        # Check duplicate count
        matching_records = [
            r for r in self.erp.registrations.values()
            if r.student_id == workflow.student.student_id and r.course_code == workflow.course.course_code
        ]
        is_exact_single_record = len(matching_records) == 1

        # Check if forced verification failure is active (for testing)
        verification_passed = (
            dest_record is not None
            and is_exact_single_record
            and (replay_code in (200, "EXISTING_REGISTRATION"))
            and not self.force_verification_failure
        )

        if verification_passed:
            rec_end = time.time()
            incident.recovery_seconds = round(rec_end - rec_start, 4)
            incident.recovery_duration_ms = round(incident.recovery_seconds * 1000, 2)
            incident.status = IncidentStatus.RECOVERED
            incident.recovered_at = now_iso()
            self.incidents[incident.incident_id] = incident
            self.db.save_incident(incident.model_dump())

            self.log_event("zenkai_recovery", "RECOVERY_VERIFIED", {
                "destination_registration_id": dest_record.registration_id,
                "recovery_seconds": incident.recovery_seconds,
                "retry_count": incident.retry_count,
            }, workflow_id=workflow.workflow_id, incident_id=incident.incident_id, reason="Destination outcome verified")
            return incident

        else:
            # VERIFICATION FAILED: ROLLBACK ADAPTERS TO SNAPSHOT
            self.log_event("zenkai_recovery", "VERIFICATION_FAILED_INITIATING_ROLLBACK", {
                "reason": "Destination verification condition not met or forced failure"
            }, workflow_id=workflow.workflow_id, incident_id=incident.incident_id, reason="Outcome verification failed")

            # Restore adapter snapshot
            self.db.clear_all_adapters_and_restore(adapter_snapshot) if hasattr(self.db, "clear_all_adapters_and_restore") else None
            # Remove current adapters and restore snapshot
            for k in list(self.active_adapters.keys()):
                self.db.delete_adapter(k)
            for k, v in adapter_snapshot.items():
                self.db.save_adapter(k, v, "v2.4", now_iso())
            self.active_adapters = self.db.list_adapters()

            # Verify the rollback succeeded
            rollback_verified = (self.active_adapters == adapter_snapshot)
            if rollback_verified:
                incident.status = IncidentStatus.ROLLED_BACK
            else:
                incident.status = IncidentStatus.VERIFICATION_FAILED

            self.db.save_incident(incident.model_dump())
            self.log_event("zenkai_recovery", "ROLLBACK_COMPLETED", {
                "status": incident.status.value,
                "rollback_verified": rollback_verified,
            }, workflow_id=workflow.workflow_id, incident_id=incident.incident_id, reason="Restored previous adapter state")
            return incident

    def approve_incident_and_resume(
        self,
        incident_id: str,
        proposal_hash: str,
        approved_by: str = "security_admin",
    ) -> WorkflowRecord:
        """Approves a pending incident strictly bound to proposal_hash, rejecting stale/tampered hashes."""
        incident = self.get_incident(incident_id)
        if not incident:
            raise ValueError(f"Incident {incident_id} not found")

        if incident.status != IncidentStatus.PENDING_APPROVAL:
            raise ValueError(f"Incident {incident_id} is in status '{incident.status}', cannot approve (must be PENDING_APPROVAL).")

        proposal = incident.patch_proposal
        if not proposal:
            raise ValueError("Incident has no proposed patch")

        # Verify proposal hash matches canonical computation
        expected_hash = compute_proposal_hash(proposal.patched_payload, incident.incident_id, incident.schema_version)
        if proposal_hash != expected_hash:
            raise ValueError(f"Mismatched or tampered proposal hash. Expected {expected_hash}, got {proposal_hash}")

        # Check if already approved/executed
        existing_approval = self.db.get_approval_by_hash(proposal_hash)
        if existing_approval and existing_approval["executed"] == 1:
            raise ValueError("Approval already executed for this proposal hash. Double-approval rejected.")

        # Record and atomically lock approval
        app_id = f"APP-{uuid.uuid4().hex[:8].upper()}"
        recorded = self.db.record_approval(app_id, incident_id, proposal_hash, approved_by, now_iso())
        if not recorded:
            raise ValueError("Approval record conflict: hash already registered.")

        if not self.db.mark_approval_executed(app_id):
            raise ValueError("Double-approval race detected: execution already claimed.")

        self.log_event("zenkai_approval_gate", "APPROVAL_CONFIRMED", {
            "approval_id": app_id,
            "proposal_hash": proposal_hash,
            "approved_by": approved_by,
        }, workflow_id=incident.workflow_id, incident_id=incident.incident_id, reason="Human authorization verified")

        # Re-validate via Deterministic Validator
        val = self.validator.validate(incident.raw_payload, proposal)
        if not val.is_valid:
            incident.status = IncidentStatus.BLOCKED
            self.db.save_incident(incident.model_dump())
            workflow = self.get_workflow(incident.workflow_id)
            workflow.status = StepStatus.FAILED
            self.db.save_workflow(workflow.model_dump())
            return workflow

        # Execute verified recovery
        rec_start = time.time()
        incident = self._execute_verified_recovery(self.get_workflow(incident.workflow_id), incident, rec_start)

        workflow = self.get_workflow(incident.workflow_id)
        s3 = workflow.steps[StepName.REGISTRATION]

        if incident.status == IncidentStatus.RECOVERED:
            s3.status = StepStatus.RECOVERED
            s3.details = "Recovered via approved patch and verified in ERP"

            # Resume Step 4: LMS Course Access
            s4 = workflow.steps[StepName.COURSE_ACCESS]
            s4.status = StepStatus.RUNNING
            lms_code, lms_data = self.lms.provision_access(workflow.student.student_id, workflow.course.course_code)
            if lms_code == 200:
                s4.status = StepStatus.SUCCESS
                s4.details = f"LMS provisioned: {lms_data['record']['lms_url']}"
                workflow.status = StepStatus.SUCCESS
                self.metrics["completed_workflows"] += 1
                self.metrics["healed_workflows"] += 1
        elif incident.status in (IncidentStatus.ROLLED_BACK, IncidentStatus.VERIFICATION_FAILED):
            s3.status = StepStatus.ROLLED_BACK
            workflow.status = StepStatus.ROLLED_BACK
        else:
            s3.status = StepStatus.FAILED
            workflow.status = StepStatus.FAILED

        self.db.save_workflow(workflow.model_dump())
        return workflow

    def run_sustained_check(self, count_per_phase: int = 5) -> Dict[str, Any]:
        """Sustained check sending steady traffic before, during, and after recovery."""
        results = {
            "before_success": 0,
            "before_total": count_per_phase,
            "during_success": 0,
            "during_total": count_per_phase,
            "after_success": 0,
            "after_total": count_per_phase,
            "recovery_seconds": 0.0,
        }

        # Clear adapters to start fresh
        for k in list(self.db.list_adapters().keys()):
            self.db.delete_adapter(k)
        self.active_adapters = {}

        # Phase 1: Before failure (Normal traffic with v2 compliant payloads)
        for i in range(count_per_phase):
            code, _ = self.erp.register({
                "student_id": f"STU-PRE-{i}",
                "course_code": "CS-499",
                "term_id": "SEM-2026-FALL",
                "idempotency_key": f"IDEMP-PRE-{i}",
            })
            if code == 200:
                results["before_success"] += 1

        # Phase 2: During failure (Legacy client sends semester_id to v2.4 ERP)
        for i in range(count_per_phase):
            code, _ = self.erp.register({
                "student_id": f"STU-DUR-{i}",
                "course_code": "CS-499",
                "semester_id": "SEM-2026-FALL",
                "idempotency_key": f"IDEMP-DUR-{i}",
            })
            if code == 200:
                results["during_success"] += 1

        # Execute single self-healing workflow to install hot-patch
        student = Student(student_id="STU-HEAL-1", name="Heal Student", email="heal@campus.edu")
        course = Course(course_code="CS-499", title="Senior Capstone Project")
        wf = self.create_registration_journey(student, course, scenario=FailureScenario.SCHEMA_DRIFT)
        healed_wf = self.execute_journey(wf.workflow_id, auto_approve_safe_patches=True)

        inc = self.get_incident(healed_wf.incident_id)
        if inc and inc.recovery_seconds:
            results["recovery_seconds"] = inc.recovery_seconds

        # Phase 3: After recovery (Legacy client traffic passes through active hot-patch adapter)
        self.active_adapters = self.db.list_adapters()
        for i in range(count_per_phase):
            payload = {
                "student_id": f"STU-POST-{i}",
                "course_code": "CS-499",
                "semester_id": "SEM-2026-FALL",
                "idempotency_key": f"IDEMP-POST-{i}",
            }
            # Gateway applies active hot-adapter
            if "semester_id" in self.active_adapters:
                dst = self.active_adapters["semester_id"]
                payload[dst] = payload.pop("semester_id")

            code, _ = self.erp.register(payload)
            if code == 200:
                results["after_success"] += 1

        return results

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowRecord]:
        raw = self.db.get_workflow(workflow_id)
        if not raw:
            return None
        return WorkflowRecord(**raw)

    def get_incident(self, incident_id: str) -> Optional[IncidentRecord]:
        raw = self.db.get_incident(incident_id)
        if not raw:
            return None
        return IncidentRecord(**raw)

    def reset_state(self):
        """Cleans both memory and durable SQLite state for repeatable testing."""
        self.db.clear_all()
        self.finance = FinanceService()
        self.advisor = AdvisorService()
        self.erp = RegistrationERPService()
        self.lms = LearningPortalService(self.erp)
        self.validator = DeterministicValidator(self.finance, self.advisor)
        self.workflows.clear()
        self.incidents.clear()
        self.active_adapters.clear()
        self.force_verification_failure = False
        self.metrics = {
            "total_workflows": 0,
            "completed_workflows": 0,
            "failed_workflows": 0,
            "healed_workflows": 0,
            "invariants_enforced": 0,
            "hot_patches_applied": 0,
            "avg_recovery_seconds": 0.0,
        }
