from datetime import date

from pydantic import BaseModel

from app.schemas import (
    AppointmentPlan,
    ConfirmationResult,
    DocumentClassification,
    FollowUpPlan,
    RoutingDecision,
    SafetyDecision,
)


class FakeLLM:
    model_name = "deterministic-test-double"

    def __init__(self, *, routing_confidence: float = 0.95) -> None:
        self.routing_confidence = routing_confidence
        self.calls: list[str] = []

    def generate(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        payload: dict,
        output_model: type[BaseModel],
    ) -> BaseModel:
        self.calls.append(agent_name)
        if output_model is SafetyDecision:
            return SafetyDecision(
                classification="safe_administrative",
                allowed_to_continue=True,
                reason="Administrative appointment coordination request.",
                escalation_required=False,
                severity="low",
            )
        if output_model is RoutingDecision:
            text = payload["patient_request"].casefold()
            intent = "book"
            if "cancel" in text:
                intent = "cancel"
            elif "reschedule" in text:
                intent = "reschedule"
            elif "document" in text and "appointment" not in text:
                intent = "document_only"
            return RoutingDecision(
                intent=intent,
                department_name="Cardiology",
                confidence=self.routing_confidence,
                preferred_start_date=None,
                preferred_end_date=None,
                reason="The patient explicitly requested Cardiology administration.",
            )
        if output_model is AppointmentPlan:
            intent = payload["intent"]
            slots = payload["available_slots"]
            active = payload["active_appointments"]
            if intent == "book" and slots:
                return AppointmentPlan(
                    action="book",
                    selected_slot_id=slots[0]["slot_id"],
                    reason="Earliest matching available slot.",
                    user_message="A slot was selected.",
                )
            if intent == "reschedule" and slots and active:
                return AppointmentPlan(
                    action="reschedule",
                    selected_slot_id=slots[0]["slot_id"],
                    selected_appointment_id=active[0]["appointment_id"],
                    reason="Active appointment and new slot selected.",
                    user_message="The appointment was rescheduled.",
                )
            if intent == "cancel" and active:
                return AppointmentPlan(
                    action="cancel",
                    selected_appointment_id=active[0]["appointment_id"],
                    reason="The active appointment was selected.",
                    user_message="The appointment was cancelled.",
                )
            return AppointmentPlan(
                action="needs_input",
                reason="No valid option is available.",
                user_message="Please provide another date or appointment.",
            )
        if output_model is DocumentClassification:
            filename = payload["filename"].casefold()
            document_type = "ecg" if "ecg" in filename else "blood_report"
            return DocumentClassification(
                document_type=document_type,
                confidence=0.97,
                document_date=date(2026, 7, 1),
                prompt_injection_detected=payload["deterministic_injection_signal"],
                reason="Filename and document headings match the selected type.",
            )
        if output_model is FollowUpPlan:
            return FollowUpPlan(
                create_appointment_reminder=True,
                reminder_hours_before=24,
                create_follow_up_task=True,
                follow_up_days_after=7,
                reason="Standard administrative reminder policy.",
            )
        if output_model is ConfirmationResult:
            facts = payload["persisted_facts"]
            return ConfirmationResult(
                message=(
                    f"Request {facts['request_reference']} was processed from persisted records."
                )
            )
        raise AssertionError(f"Unexpected output model: {output_model}")
