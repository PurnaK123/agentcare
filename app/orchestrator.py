import uuid
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.agents import (
    AppointmentAgent,
    CoordinatorAgent,
    DepartmentRoutingAgent,
    DocumentAgent,
    FollowUpAgent,
    LLMClient,
    SafetyAgent,
)
from app.config import get_settings
from app.database import utc_now
from app.models import (
    AgentStep,
    AgentStepStatus,
    Appointment,
    ApprovalRequest,
    ApprovalStatus,
    DocumentStatus,
    Escalation,
    EscalationStatus,
    PatientDocument,
    PatientProfile,
    PatientRequest,
    RequestStatus,
    User,
    WorkflowRun,
    WorkflowStatus,
)
from app.services.audit import record_audit
from app.services.documents import stage_document
from app.services.follow_up import create_confirmation_notification
from app.tools import ToolGateway


class WorkflowError(RuntimeError):
    pass


WorkflowStage = Literal[
    "intake",
    "safety",
    "routing",
    "approval",
    "appointment",
    "documents",
    "follow_up",
    "confirmation",
]


class WorkflowGraphState(TypedDict):
    workflow_run_id: int
    completed_steps: int


class WorkflowCoordinator:
    def __init__(self, db: Session, llm: LLMClient, actor: User | None = None) -> None:
        self.db = db
        self.llm = llm
        self.actor = actor
        self.settings = get_settings()
        self.coordinator = CoordinatorAgent(llm)
        self.safety = SafetyAgent(llm)
        self.routing = DepartmentRoutingAgent(llm)
        self.appointment = AppointmentAgent(llm)
        self.document = DocumentAgent(llm)
        self.follow_up = FollowUpAgent(llm)

    def submit(
        self,
        *,
        patient: PatientProfile,
        request_text: str,
        uploads: list[tuple[str, bytes]] | None = None,
    ) -> WorkflowRun:
        cleaned_text = " ".join(request_text.split()).strip()
        if not 5 <= len(cleaned_text) <= 2000:
            raise ValueError("Request must contain between 5 and 2,000 characters")
        patient_request = PatientRequest(
            patient_id=patient.id,
            request_text=cleaned_text,
            status=RequestStatus.SUBMITTED,
        )
        self.db.add(patient_request)
        self.db.flush()
        run = WorkflowRun(
            request_id=patient_request.id,
            patient_id=patient.id,
            correlation_id=str(uuid.uuid4()),
            current_step="intake",
            status=WorkflowStatus.RUNNING,
            state={},
        )
        self.db.add(run)
        self.db.flush()
        for filename, content in uploads or []:
            stage_document(
                self.db,
                patient_id=patient.id,
                request_id=patient_request.id,
                original_name=filename,
                content=content,
                correlation_id=run.correlation_id,
            )
        record_audit(
            self.db,
            action="workflow.submitted",
            entity_type="workflow_run",
            entity_id=run.id,
            actor=self.actor,
            actor_role="patient",
            correlation_id=run.correlation_id,
            metadata={"request_id": patient_request.id, "upload_count": len(uploads or [])},
        )
        self.db.commit()
        return self.execute(run.id)

    def execute(self, workflow_run_id: int) -> WorkflowRun:
        run = self._load_run(workflow_run_id)
        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.ESCALATED}:
            return run
        if run.status == WorkflowStatus.WAITING_FOR_APPROVAL:
            approval = self._latest_approval(run.id)
            if not approval or approval.status == ApprovalStatus.PENDING:
                return run
        run.status = WorkflowStatus.RUNNING
        run.request.status = RequestStatus.PROCESSING
        run.attempt_count += 1
        run.last_error = None
        self.db.commit()
        try:
            graph = self._build_workflow_graph()
            graph.invoke(
                {"workflow_run_id": run.id, "completed_steps": 0},
                config={"recursion_limit": self.settings.max_workflow_steps + 1},
            )
        except Exception as exc:
            self.db.rollback()
            run = self._load_run(workflow_run_id)
            failed_agent = {
                "intake": self.coordinator,
                "safety": self.safety,
                "routing": self.routing,
                "approval": self.coordinator,
                "appointment": self.appointment,
                "documents": self.document,
                "follow_up": self.follow_up,
                "confirmation": self.coordinator,
            }.get(run.current_step, self.coordinator)
            failed_step = self._start_step(
                run,
                failed_agent,
                {"recovery_checkpoint": run.current_step, "attempt": run.attempt_count},
            )
            failed_step.status = AgentStepStatus.FAILED
            failed_step.error = type(exc).__name__
            failed_step.completed_at = utc_now()
            run.status = WorkflowStatus.RETRY_PENDING
            run.request.status = RequestStatus.RETRY_PENDING
            run.last_error = f"{type(exc).__name__}: workflow stage failed"
            record_audit(
                self.db,
                action="workflow.retry_pending",
                entity_type="workflow_run",
                entity_id=run.id,
                actor_role="coordinator_agent",
                correlation_id=run.correlation_id,
                metadata={"stage": run.current_step, "error_type": type(exc).__name__},
            )
            self.db.commit()
        return self._load_run(workflow_run_id)

    def _build_workflow_graph(self):
        graph = StateGraph(WorkflowGraphState)
        stages: tuple[WorkflowStage, ...] = (
            "intake",
            "safety",
            "routing",
            "approval",
            "appointment",
            "documents",
            "follow_up",
            "confirmation",
        )
        for stage in stages:
            graph.add_node(stage, self._graph_stage_node(stage))
        graph.add_conditional_edges(START, self._graph_next_stage)
        for stage in stages:
            graph.add_conditional_edges(stage, self._graph_next_stage)
        return graph.compile()

    def _graph_stage_node(self, stage: WorkflowStage):
        def run_stage(state: WorkflowGraphState) -> dict[str, int]:
            run = self._load_run(state["workflow_run_id"])
            if run.status != WorkflowStatus.RUNNING or run.current_step != stage:
                return {"completed_steps": state["completed_steps"]}
            completed_steps = state["completed_steps"] + 1
            if completed_steps > self.settings.max_workflow_steps:
                raise WorkflowError("Workflow exceeded its configured step limit")
            getattr(self, f"_stage_{stage}")(run)
            self.db.commit()
            return {"completed_steps": completed_steps}

        return run_stage

    def _graph_next_stage(self, state: WorkflowGraphState) -> WorkflowStage | str:
        run = self._load_run(state["workflow_run_id"])
        if run.status != WorkflowStatus.RUNNING:
            return END
        if run.current_step == "complete":
            return END
        if run.current_step not in {
            "intake",
            "safety",
            "routing",
            "approval",
            "appointment",
            "documents",
            "follow_up",
            "confirmation",
        }:
            raise WorkflowError(f"Unknown workflow stage: {run.current_step}")
        return run.current_step

    def _load_run(self, workflow_run_id: int) -> WorkflowRun:
        result = self.db.execute(
            select(WorkflowRun)
            .options(
                joinedload(WorkflowRun.request).joinedload(PatientRequest.patient),
                joinedload(WorkflowRun.steps),
            )
            .where(WorkflowRun.id == workflow_run_id)
        ).unique()
        run = result.scalar_one_or_none()
        if not run:
            raise WorkflowError("Workflow run not found")
        return run

    def _latest_approval(self, workflow_run_id: int) -> ApprovalRequest | None:
        return self.db.scalar(
            select(ApprovalRequest)
            .where(ApprovalRequest.workflow_run_id == workflow_run_id)
            .order_by(ApprovalRequest.id.desc())
        )

    def _start_step(self, run: WorkflowRun, agent: Any, input_summary: dict) -> AgentStep:
        sequence = self.db.scalar(
            select(func.coalesce(func.max(AgentStep.sequence), 0)).where(
                AgentStep.workflow_run_id == run.id
            )
        )
        step = AgentStep(
            workflow_run_id=run.id,
            sequence=int(sequence or 0) + 1,
            agent_name=agent.name,
            prompt_version=agent.prompt_version,
            model_name=self.llm.model_name,
            status=AgentStepStatus.RUNNING,
            input_summary=input_summary,
        )
        self.db.add(step)
        self.db.flush()
        return step

    def _finish_step(
        self,
        step: AgentStep,
        *,
        output: dict[str, Any],
        gateway: ToolGateway,
        blocked: bool = False,
    ) -> None:
        step.output = output
        step.tool_calls = gateway.calls
        step.status = AgentStepStatus.BLOCKED if blocked else AgentStepStatus.COMPLETED
        step.completed_at = utc_now()

    @staticmethod
    def _set_state(run: WorkflowRun, key: str, value: Any) -> None:
        state = dict(run.state or {})
        state[key] = value
        run.state = state
        run.version += 1

    def _stage_intake(self, run: WorkflowRun) -> None:
        gateway = ToolGateway(self.db, run, self.coordinator.name)
        step = self._start_step(run, self.coordinator, {"request_id": run.request_id})
        patient_context = gateway.patient_record()
        self._set_state(run, "patient", patient_context)
        self._finish_step(step, output={"handoff": "safety"}, gateway=gateway)
        run.current_step = "safety"

    def _stage_safety(self, run: WorkflowRun) -> None:
        gateway = ToolGateway(self.db, run, self.safety.name)
        step = self._start_step(run, self.safety, {"request_length": len(run.request.request_text)})
        decision = self.safety.evaluate(run.request.request_text)
        self._set_state(run, "safety", decision.model_dump(mode="json"))
        if not decision.allowed_to_continue:
            if decision.escalation_required:
                gateway.create_escalation(
                    reason_code=decision.classification,
                    reason=decision.reason,
                    severity=decision.severity,
                )
            self._finish_step(
                step, output=decision.model_dump(mode="json"), gateway=gateway, blocked=True
            )
            run.status = WorkflowStatus.ESCALATED
            run.request.status = RequestStatus.ESCALATED
            if decision.classification == "emergency":
                run.request.confirmation_message = (
                    "This administrative service cannot assess urgent symptoms. "
                    f"Please call {self.settings.emergency_number} or go to the nearest "
                    "emergency department now. A staff escalation has been recorded."
                )
            else:
                run.request.confirmation_message = (
                    "This request cannot continue automatically and has been sent for staff review."
                )
            return
        self._finish_step(step, output=decision.model_dump(mode="json"), gateway=gateway)
        run.current_step = "routing"

    def _stage_routing(self, run: WorkflowRun) -> None:
        gateway = ToolGateway(self.db, run, self.routing.name)
        step = self._start_step(run, self.routing, {"handoff_from": "safety"})
        departments = gateway.department_lookup()
        decision = self.routing.route(
            request_text=run.request.request_text,
            departments=departments,
            current_local_datetime=datetime.now(ZoneInfo(self.settings.timezone)),
        )
        department = next(
            (
                item
                for item in departments
                if decision.department_name
                and item["name"].casefold() == decision.department_name.casefold()
            ),
            None,
        )
        if decision.department_name and not department:
            decision.needs_clarification = True
            decision.clarification_question = "Which listed hospital department do you need?"
        route_state = decision.model_dump(mode="json")
        route_state["department_id"] = department["id"] if department else None
        route_state["required_document_types"] = (
            department["required_document_types"] if department else []
        )
        self._set_state(run, "routing", route_state)
        self._finish_step(step, output=route_state, gateway=gateway)
        if decision.intent == "unsupported":
            safety_gateway = ToolGateway(self.db, run, self.safety.name)
            safety_gateway.create_escalation(
                reason_code="unsupported_request",
                reason="The request is outside AgentCare's administrative scope.",
                severity="medium",
            )
            step.tool_calls = [*step.tool_calls, *safety_gateway.calls]
            run.status = WorkflowStatus.ESCALATED
            run.request.status = RequestStatus.ESCALATED
            run.request.confirmation_message = "This request requires review by hospital staff."
            return
        if decision.needs_clarification or (decision.department_name and not department):
            run.status = WorkflowStatus.WAITING_FOR_INPUT
            run.request.status = RequestStatus.NEEDS_INPUT
            run.request.confirmation_message = decision.clarification_question or (
                "Please provide the department needed for this request."
            )
            return
        if decision.confidence < 0.5:
            run.status = WorkflowStatus.WAITING_FOR_INPUT
            run.request.status = RequestStatus.NEEDS_INPUT
            run.request.confirmation_message = (
                "The department could not be identified confidently. Please submit a new "
                "request naming the hospital department."
            )
            return
        if department and 0.5 <= decision.confidence < 0.72:
            coordinator_gateway = ToolGateway(self.db, run, self.coordinator.name)
            coordinator_gateway.create_approval(
                action_type="department_route",
                action_payload=route_state,
            )
            step.tool_calls = [*step.tool_calls, *coordinator_gateway.calls]
            run.current_step = "approval"
            run.status = WorkflowStatus.WAITING_FOR_APPROVAL
            run.request.status = RequestStatus.PENDING_APPROVAL
            run.request.confirmation_message = (
                "The proposed department route is awaiting staff approval."
            )
            return
        run.current_step = "appointment"

    def _stage_approval(self, run: WorkflowRun) -> None:
        approval = self._latest_approval(run.id)
        if not approval or approval.status == ApprovalStatus.PENDING:
            run.status = WorkflowStatus.WAITING_FOR_APPROVAL
            return
        if approval.status != ApprovalStatus.APPROVED:
            run.status = WorkflowStatus.ESCALATED
            run.request.status = RequestStatus.ESCALATED
            run.request.confirmation_message = "Staff did not approve the proposed action."
            return
        if approval.action_type == "department_route":
            self._set_state(run, "routing", approval.action_payload)
            run.current_step = "appointment"
            return
        raise WorkflowError("Unsupported approved action")

    def _stage_appointment(self, run: WorkflowRun) -> None:
        route = run.state.get("routing", {})
        intent = route.get("intent", "general_admin")
        gateway = ToolGateway(self.db, run, self.appointment.name)
        step = self._start_step(run, self.appointment, {"intent": intent})
        if intent in {"document_only", "profile_update", "general_admin"}:
            self._finish_step(step, output={"action": "none"}, gateway=gateway)
            run.current_step = "documents"
            return
        active = gateway.active_appointments()
        slots: list[dict[str, Any]] = []
        department_id = route.get("department_id")
        if intent in {"book", "reschedule"} and department_id:
            slots = gateway.appointment_availability(
                department_id=department_id,
                start_date=self._parse_date(route.get("preferred_start_date")),
                end_date=self._parse_date(route.get("preferred_end_date")),
            )
        plan = self.appointment.plan(
            request_text=run.request.request_text,
            intent=intent,
            available_slots=slots,
            active_appointments=active,
        )
        if plan.action not in {intent, "none", "needs_input"}:
            plan.action = "needs_input"
            plan.selected_slot_id = None
            plan.selected_appointment_id = None
            plan.reason = "The proposed action did not match the patient's validated intent."
            plan.user_message = "Please restate the appointment action you want to perform."
        valid_slot_ids = {item["slot_id"] for item in slots}
        valid_appointment_ids = {item["appointment_id"] for item in active}
        appointment: Appointment | None = None
        if plan.action == "book" and plan.selected_slot_id in valid_slot_ids:
            appointment = gateway.book_appointment(
                slot_id=plan.selected_slot_id,
                reason=run.request.request_text,
            )
        elif (
            plan.action == "reschedule"
            and plan.selected_slot_id in valid_slot_ids
            and plan.selected_appointment_id in valid_appointment_ids
        ):
            appointment = gateway.reschedule_appointment(
                appointment_id=plan.selected_appointment_id,
                slot_id=plan.selected_slot_id,
            )
        elif plan.action == "cancel" and plan.selected_appointment_id in valid_appointment_ids:
            appointment = gateway.cancel_appointment(
                appointment_id=plan.selected_appointment_id,
                reason=run.request.request_text,
            )
        elif plan.action not in {"none", "needs_input"}:
            plan.action = "needs_input"
            plan.user_message = "A valid appointment or slot could not be selected."
        appointment_state = plan.model_dump(mode="json")
        appointment_state["appointment_id"] = appointment.id if appointment else None
        self._set_state(run, "appointment", appointment_state)
        self._finish_step(step, output=appointment_state, gateway=gateway)
        if plan.action == "needs_input":
            run.status = WorkflowStatus.WAITING_FOR_INPUT
            run.request.status = RequestStatus.NEEDS_INPUT
            run.request.confirmation_message = plan.user_message
            return
        run.current_step = "documents"

    def _stage_documents(self, run: WorkflowRun) -> None:
        gateway = ToolGateway(self.db, run, self.document.name)
        step = self._start_step(run, self.document, {"request_id": run.request_id})
        pending = list(
            self.db.scalars(
                select(PatientDocument).where(
                    PatientDocument.request_id == run.request_id,
                    PatientDocument.status == DocumentStatus.PENDING,
                )
            )
        )
        classifications: list[dict[str, Any]] = []
        review_required = False
        for item in pending:
            preview, injection_signal = gateway.document_preview(item)
            classification = self.document.classify(
                filename=item.original_name,
                mime_type=item.mime_type,
                preview=preview,
                injection_signal=injection_signal,
            )
            updated = gateway.classify_document(item.id, classification)
            classifications.append(
                {
                    "document_id": updated.id,
                    "type": updated.document_type,
                    "status": updated.status.value,
                }
            )
            if updated.status == DocumentStatus.NEEDS_REVIEW:
                review_required = True
                gateway.create_escalation(
                    reason_code="document_review",
                    reason="A document classification requires staff review.",
                    severity="medium",
                )
        required_types = run.state.get("routing", {}).get("required_document_types", [])
        missing = gateway.missing_documents(required_types)
        document_state = {"classifications": classifications, "missing_document_types": missing}
        self._set_state(run, "documents", document_state)
        self._finish_step(step, output=document_state, gateway=gateway)
        self._set_state(run, "document_review_required", review_required)
        run.current_step = "follow_up"

    def _stage_follow_up(self, run: WorkflowRun) -> None:
        gateway = ToolGateway(self.db, run, self.follow_up.name)
        step = self._start_step(run, self.follow_up, {"handoff_from": "document"})
        appointment_id = run.state.get("appointment", {}).get("appointment_id")
        action = run.state.get("appointment", {}).get("action")
        reminder_ids: list[int] = []
        if appointment_id and action in {"book", "reschedule"}:
            appointment = self.db.get(Appointment, appointment_id)
            if not appointment:
                raise WorkflowError("Persisted appointment was not found")
            plan = self.follow_up.plan(
                {
                    "status": appointment.status.value,
                    "scheduled_start": appointment.scheduled_start.isoformat(),
                    "scheduled_end": appointment.scheduled_end.isoformat(),
                }
            )
            reminders = gateway.create_reminders(appointment.id, plan)
            reminder_ids = [item.id for item in reminders]
            output = {"plan": plan.model_dump(mode="json"), "reminder_ids": reminder_ids}
        else:
            output = {"plan": "not_required", "reminder_ids": []}
        self._set_state(run, "follow_up", {"reminder_ids": reminder_ids})
        self._finish_step(step, output=output, gateway=gateway)
        run.current_step = "confirmation"

    def _stage_confirmation(self, run: WorkflowRun) -> None:
        gateway = ToolGateway(self.db, run, self.coordinator.name)
        step = self._start_step(run, self.coordinator, {"handoff_from": "follow_up"})
        facts = self._persisted_facts(run)
        result = self.coordinator.confirm(facts)
        run.request.confirmation_message = result.message
        appointment_id = run.state.get("appointment", {}).get("appointment_id")
        if appointment_id:
            appointment = self.db.get(Appointment, appointment_id)
            if appointment:
                create_confirmation_notification(
                    self.db, appointment=appointment, message=result.message
                )
        self._finish_step(step, output={"message": result.message}, gateway=gateway)
        has_open_escalation = bool(
            self.db.scalar(
                select(Escalation.id).where(
                    Escalation.workflow_run_id == run.id,
                    Escalation.status == EscalationStatus.OPEN,
                )
            )
        )
        if has_open_escalation:
            run.status = WorkflowStatus.ESCALATED
            run.request.status = RequestStatus.ESCALATED
        else:
            run.status = WorkflowStatus.COMPLETED
            run.request.status = RequestStatus.COMPLETED
        run.current_step = "complete"
        record_audit(
            self.db,
            action="workflow.completed" if not has_open_escalation else "workflow.escalated",
            entity_type="workflow_run",
            entity_id=run.id,
            actor_role="coordinator_agent",
            correlation_id=run.correlation_id,
            metadata={"status": run.status.value},
        )

    def _persisted_facts(self, run: WorkflowRun) -> dict[str, Any]:
        route = run.state.get("routing", {})
        appointment_state = run.state.get("appointment", {})
        appointment = None
        if appointment_state.get("appointment_id"):
            appointment = self.db.get(Appointment, appointment_state["appointment_id"])
        facts: dict[str, Any] = {
            "request_reference": f"REQ-{run.request_id:05d}",
            "intent": route.get("intent"),
            "department": route.get("department_name"),
            "appointment_action": appointment_state.get("action"),
            "missing_document_types": run.state.get("documents", {}).get(
                "missing_document_types", []
            ),
            "document_review_required": run.state.get("document_review_required", False),
            "reminders_created": len(run.state.get("follow_up", {}).get("reminder_ids", [])),
        }
        if appointment:
            doctor = self.db.scalar(
                select(Appointment)
                .options(joinedload(Appointment.doctor))
                .where(Appointment.id == appointment.id)
            )
            scheduled_start = appointment.scheduled_start
            if scheduled_start.tzinfo is None:
                scheduled_start = scheduled_start.replace(tzinfo=UTC)
            local_start = scheduled_start.astimezone(ZoneInfo(self.settings.timezone))
            facts["appointment"] = {
                "status": appointment.status.value,
                "doctor": doctor.doctor.name if doctor else "",
                "scheduled_start_local": local_start.isoformat(),
                "timezone": self.settings.timezone,
            }
        return facts

    @staticmethod
    def _parse_date(value: str | None):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
