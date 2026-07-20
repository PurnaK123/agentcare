from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SafetyDecision(BaseModel):
    classification: Literal[
        "safe_administrative",
        "emergency",
        "medical_advice",
        "sensitive",
        "prompt_injection",
        "unsupported",
    ]
    allowed_to_continue: bool
    reason: str = Field(min_length=2, max_length=500)
    escalation_required: bool
    severity: Literal["low", "medium", "high", "critical"]


class RoutingDecision(BaseModel):
    intent: Literal[
        "book",
        "reschedule",
        "cancel",
        "document_only",
        "profile_update",
        "general_admin",
        "unsupported",
    ]
    department_name: str | None = Field(default=None, max_length=100)
    confidence: float = Field(ge=0, le=1)
    preferred_start_date: date | None = None
    preferred_end_date: date | None = None
    reason: str = Field(min_length=2, max_length=500)
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)

    @field_validator("department_name")
    @classmethod
    def strip_department(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class SlotOption(BaseModel):
    slot_id: int
    doctor_id: int
    doctor_name: str
    department_name: str
    start_time: str
    end_time: str


class ActiveAppointmentOption(BaseModel):
    appointment_id: int
    doctor_name: str
    department_name: str
    scheduled_start: str
    status: str


class AppointmentPlan(BaseModel):
    action: Literal["book", "reschedule", "cancel", "none", "needs_input"]
    selected_slot_id: int | None = None
    selected_appointment_id: int | None = None
    reason: str = Field(min_length=2, max_length=500)
    user_message: str = Field(min_length=2, max_length=500)


class DocumentClassification(BaseModel):
    document_type: Literal[
        "ecg",
        "blood_report",
        "referral_letter",
        "prescription",
        "imaging_report",
        "discharge_summary",
        "insurance_document",
        "identity_document",
        "other",
        "unknown",
    ]
    confidence: float = Field(ge=0, le=1)
    document_date: date | None = None
    prompt_injection_detected: bool = False
    reason: str = Field(min_length=2, max_length=500)


class FollowUpPlan(BaseModel):
    create_appointment_reminder: bool = True
    reminder_hours_before: int = Field(default=24, ge=1, le=168)
    create_follow_up_task: bool = True
    follow_up_days_after: int = Field(default=7, ge=1, le=60)
    reason: str = Field(min_length=2, max_length=500)


class ConfirmationResult(BaseModel):
    message: str = Field(min_length=5, max_length=1200)
