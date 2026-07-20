from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Appointment, Department, Doctor, PatientDocument, WorkflowRun
from app.schemas import DocumentClassification, FollowUpPlan
from app.security import redact_metadata
from app.services import appointments, documents, follow_up, oversight

ToolReturn = TypeVar("ToolReturn")


class ToolPermissionError(PermissionError):
    pass


ALLOWED_TOOLS: dict[str, set[str]] = {
    "coordinator": {"patient_record", "create_approval"},
    "safety": {"create_escalation"},
    "department_routing": {"department_lookup"},
    "appointment": {
        "appointment_availability",
        "active_appointments",
        "book_appointment",
        "reschedule_appointment",
        "cancel_appointment",
    },
    "document": {
        "document_preview",
        "classify_document",
        "missing_documents",
        "create_escalation",
    },
    "follow_up": {"create_reminders"},
}


class ToolGateway:
    def __init__(self, db: Session, workflow_run: WorkflowRun, agent_name: str) -> None:
        self.db = db
        self.workflow_run = workflow_run
        self.agent_name = agent_name
        self.calls: list[dict[str, Any]] = []

    def _invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        operation: Callable[[], ToolReturn],
        serializer: Callable[[ToolReturn], Any] | None = None,
    ) -> ToolReturn:
        if tool_name not in ALLOWED_TOOLS.get(self.agent_name, set()):
            raise ToolPermissionError(
                f"Agent {self.agent_name} is not permitted to invoke {tool_name}"
            )
        call: dict[str, Any] = {
            "tool": tool_name,
            "arguments": redact_metadata(arguments),
            "status": "running",
        }
        self.calls.append(call)
        try:
            result = operation()
            serialized = serializer(result) if serializer else result
            call["status"] = "completed"
            call["result"] = redact_metadata(serialized)
            return result
        except Exception as exc:
            call["status"] = "failed"
            call["error"] = type(exc).__name__
            raise

    def patient_record(self) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            patient = self.workflow_run.request.patient
            return {
                "patient_id": patient.id,
                "synthetic_reference": patient.synthetic_reference,
                "profile_present": True,
            }

        return self._invoke("patient_record", {}, operation)

    def department_lookup(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            departments = list(
                self.db.scalars(
                    select(Department).where(Department.active.is_(True)).order_by(Department.name)
                )
            )
            return [
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                    "required_document_types": item.required_document_types,
                }
                for item in departments
            ]

        return self._invoke("department_lookup", {}, operation)

    def appointment_availability(
        self,
        *,
        department_id: int,
        start_date: date | None,
        end_date: date | None,
    ) -> list[dict[str, Any]]:
        def serialize_slot(slot) -> dict[str, Any]:
            return {
                "slot_id": slot.id,
                "doctor_id": slot.doctor_id,
                "doctor_name": slot.doctor.name,
                "department_name": slot.doctor.department.name,
                "start_time": slot.start_time.isoformat(),
                "end_time": slot.end_time.isoformat(),
            }

        def operation() -> list[dict[str, Any]]:
            slots = appointments.list_available_slots(
                self.db,
                department_id=department_id,
                start_date=start_date,
                end_date=end_date,
            )
            return [serialize_slot(slot) for slot in slots]

        return self._invoke(
            "appointment_availability",
            {
                "department_id": department_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            operation,
        )

    def active_appointments(self) -> list[dict[str, Any]]:
        def operation() -> list[dict[str, Any]]:
            active = appointments.list_active_appointments(
                self.db, self.workflow_run.patient_id
            )
            return [
                {
                    "appointment_id": item.id,
                    "doctor_name": item.doctor.name,
                    "department_name": item.doctor.department.name,
                    "scheduled_start": item.scheduled_start.isoformat(),
                    "status": item.status.value,
                }
                for item in active
            ]

        return self._invoke("active_appointments", {}, operation)

    def book_appointment(self, *, slot_id: int, reason: str):
        return self._invoke(
            "book_appointment",
            {"slot_id": slot_id, "reason": reason},
            lambda: appointments.book_slot(
                self.db,
                patient_id=self.workflow_run.patient_id,
                slot_id=slot_id,
                reason=reason,
                workflow_run_id=self.workflow_run.id,
                correlation_id=self.workflow_run.correlation_id,
            ),
            lambda item: {"appointment_id": item.id, "status": item.status.value},
        )

    def reschedule_appointment(self, *, appointment_id: int, slot_id: int):
        return self._invoke(
            "reschedule_appointment",
            {"appointment_id": appointment_id, "slot_id": slot_id},
            lambda: appointments.reschedule_appointment(
                self.db,
                patient_id=self.workflow_run.patient_id,
                appointment_id=appointment_id,
                new_slot_id=slot_id,
                correlation_id=self.workflow_run.correlation_id,
            ),
            lambda item: {"appointment_id": item.id, "status": item.status.value},
        )

    def cancel_appointment(self, *, appointment_id: int, reason: str):
        return self._invoke(
            "cancel_appointment",
            {"appointment_id": appointment_id, "reason": reason},
            lambda: appointments.cancel_appointment(
                self.db,
                patient_id=self.workflow_run.patient_id,
                appointment_id=appointment_id,
                reason=reason,
                correlation_id=self.workflow_run.correlation_id,
            ),
            lambda item: {"appointment_id": item.id, "status": item.status.value},
        )

    def document_preview(self, document: PatientDocument) -> tuple[str, bool]:
        def operation() -> tuple[str, bool]:
            preview = documents.extract_document_preview(document)
            return preview, documents.has_prompt_injection(preview)

        return self._invoke(
            "document_preview",
            {"document_id": document.id},
            operation,
            lambda result: {
                "preview_characters": len(result[0]),
                "prompt_injection_signal": result[1],
            },
        )

    def classify_document(
        self, document_id: int, classification: DocumentClassification
    ) -> PatientDocument:
        return self._invoke(
            "classify_document",
            {"document_id": document_id, "classification": classification.model_dump(mode="json")},
            lambda: documents.apply_classification(
                self.db,
                document_id=document_id,
                classification=classification,
                correlation_id=self.workflow_run.correlation_id,
            ),
            lambda item: {
                "document_id": item.id,
                "document_type": item.document_type,
                "status": item.status.value,
            },
        )

    def missing_documents(self, required_types: list[str]) -> list[str]:
        return self._invoke(
            "missing_documents",
            {"required_types": required_types},
            lambda: documents.missing_document_types(
                self.db,
                patient_id=self.workflow_run.patient_id,
                required_types=required_types,
            ),
        )

    def create_reminders(self, appointment_id: int, plan: FollowUpPlan):
        appointment = self.db.get(Appointment, appointment_id)
        if not appointment or appointment.patient_id != self.workflow_run.patient_id:
            raise ValueError("Appointment not found for workflow patient")
        return self._invoke(
            "create_reminders",
            {"appointment_id": appointment_id, "plan": plan.model_dump(mode="json")},
            lambda: follow_up.create_follow_up_records(
                self.db,
                appointment=appointment,
                plan=plan,
                correlation_id=self.workflow_run.correlation_id,
            ),
            lambda items: {"reminder_ids": [item.id for item in items]},
        )

    def create_escalation(self, *, reason_code: str, reason: str, severity: str):
        return self._invoke(
            "create_escalation",
            {"reason_code": reason_code, "reason": reason, "severity": severity},
            lambda: oversight.create_escalation(
                self.db,
                workflow_run=self.workflow_run,
                reason_code=reason_code,
                reason=reason,
                severity=severity,
            ),
            lambda item: {"escalation_id": item.id, "status": item.status.value},
        )

    def create_approval(self, *, action_type: str, action_payload: dict[str, Any]):
        return self._invoke(
            "create_approval",
            {"action_type": action_type, "action_payload": action_payload},
            lambda: oversight.create_approval(
                self.db,
                workflow_run=self.workflow_run,
                action_type=action_type,
                action_payload=action_payload,
            ),
            lambda item: {"approval_id": item.id, "status": item.status.value},
        )

    def department_by_name(self, name: str | None) -> Department | None:
        if not name:
            return None
        return self.db.scalar(
            select(Department)
            .options(joinedload(Department.doctors).joinedload(Doctor.slots))
            .where(Department.active.is_(True), Department.name.ilike(name))
        )
