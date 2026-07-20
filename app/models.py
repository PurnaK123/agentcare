import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utc_now


def enum_type(enum_class: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
    )


class Role(enum.StrEnum):
    PATIENT = "patient"
    STAFF = "staff"


class RequestStatus(enum.StrEnum):
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    NEEDS_INPUT = "needs_input"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    RETRY_PENDING = "retry_pending"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStatus(enum.StrEnum):
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    RETRY_PENDING = "retry_pending"
    FAILED = "failed"


class AgentStepStatus(enum.StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class SlotStatus(enum.StrEnum):
    AVAILABLE = "available"
    HELD = "held"
    BOOKED = "booked"
    UNAVAILABLE = "unavailable"


class AppointmentStatus(enum.StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class DocumentStatus(enum.StrEnum):
    PENDING = "pending_classification"
    CLASSIFIED = "classified"
    DUPLICATE = "duplicate"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ReminderStatus(enum.StrEnum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EscalationStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class NotificationStatus(enum.StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(enum_type(Role, "role"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    patient_profile: Mapped["PatientProfile | None"] = relationship(
        back_populates="user", uselist=False
    )


class PatientProfile(TimestampMixin, Base):
    __tablename__ = "patient_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    synthetic_reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(40), default="English")
    emergency_contact: Mapped[str | None] = mapped_column(String(160), nullable=True)
    demo_data_acknowledged: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="patient_profile")
    requests: Mapped[list["PatientRequest"]] = relationship(back_populates="patient")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")
    documents: Mapped[list["PatientDocument"]] = relationship(back_populates="patient")


class Department(TimestampMixin, Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    required_document_types: Mapped[list[str]] = mapped_column(JSON, default=list)

    doctors: Mapped[list["Doctor"]] = relationship(back_populates="department")


class Doctor(TimestampMixin, Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    registration_reference: Mapped[str] = mapped_column(String(60), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    department: Mapped[Department] = relationship(back_populates="doctors")
    slots: Mapped[list["AppointmentSlot"]] = relationship(back_populates="doctor")

    __table_args__ = (UniqueConstraint("department_id", "name", name="uq_doctor_department_name"),)


class AppointmentSlot(TimestampMixin, Base):
    __tablename__ = "appointment_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[SlotStatus] = mapped_column(
        enum_type(SlotStatus, "slot_status"), default=SlotStatus.AVAILABLE, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)

    doctor: Mapped[Doctor] = relationship(back_populates="slots")
    appointment: Mapped["Appointment | None"] = relationship(back_populates="slot", uselist=False)

    __table_args__ = (
        UniqueConstraint("doctor_id", "start_time", name="uq_doctor_slot_start"),
        Index("ix_slot_status_start", "status", "start_time"),
    )


class PatientRequest(TimestampMixin, Base):
    __tablename__ = "patient_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient_profiles.id"), index=True)
    request_text: Mapped[str] = mapped_column(Text)
    status: Mapped[RequestStatus] = mapped_column(
        enum_type(RequestStatus, "request_status"), default=RequestStatus.SUBMITTED, index=True
    )
    confirmation_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    patient: Mapped[PatientProfile] = relationship(back_populates="requests")
    workflow_run: Mapped["WorkflowRun | None"] = relationship(
        back_populates="request", uselist=False
    )
    documents: Mapped[list["PatientDocument"]] = relationship(back_populates="request")


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("patient_requests.id"), unique=True, index=True
    )
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient_profiles.id"), index=True)
    correlation_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    current_step: Mapped[str] = mapped_column(String(60), default="safety")
    status: Mapped[WorkflowStatus] = mapped_column(
        enum_type(WorkflowStatus, "workflow_status"), default=WorkflowStatus.RUNNING, index=True
    )
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[PatientRequest] = relationship(back_populates="workflow_run")
    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="workflow_run", order_by="AgentStep.sequence"
    )
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="workflow_run")
    approvals: Mapped[list["ApprovalRequest"]] = relationship(back_populates="workflow_run")


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    agent_name: Mapped[str] = mapped_column(String(80), index=True)
    prompt_version: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[AgentStepStatus] = mapped_column(
        enum_type(AgentStepStatus, "agent_step_status"), default=AgentStepStatus.RUNNING
    )
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="steps")

    __table_args__ = (
        UniqueConstraint("workflow_run_id", "sequence", name="uq_workflow_step_sequence"),
    )


class Appointment(TimestampMixin, Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient_profiles.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    slot_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointment_slots.id"), nullable=True, unique=True
    )
    workflow_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=True, index=True
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        enum_type(AppointmentStatus, "appointment_status"),
        default=AppointmentStatus.CONFIRMED,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    patient: Mapped[PatientProfile] = relationship(back_populates="appointments")
    doctor: Mapped[Doctor] = relationship()
    slot: Mapped[AppointmentSlot | None] = relationship(back_populates="appointment")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="appointment")


class PatientDocument(TimestampMixin, Base):
    __tablename__ = "patient_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient_profiles.id"), index=True)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("patient_requests.id"), nullable=True, index=True
    )
    document_type: Mapped[str] = mapped_column(String(80), default="unknown", index=True)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_type(DocumentStatus, "document_status"), default=DocumentStatus.PENDING, index=True
    )
    original_name: Mapped[str] = mapped_column(String(255))
    storage_reference: Mapped[str] = mapped_column(String(500), index=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_injection_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("patient_documents.id"), nullable=True
    )

    patient: Mapped[PatientProfile] = relationship(back_populates="documents")
    request: Mapped[PatientRequest | None] = relationship(back_populates="documents")
    duplicate_of: Mapped["PatientDocument | None"] = relationship(remote_side="PatientDocument.id")


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient_profiles.id"), index=True)
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id"), nullable=True, index=True
    )
    reminder_type: Mapped[str] = mapped_column(String(60))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[ReminderStatus] = mapped_column(
        enum_type(ReminderStatus, "reminder_status"), default=ReminderStatus.SCHEDULED
    )
    message: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    appointment: Mapped[Appointment | None] = relationship(back_populates="reminders")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient_profiles.id"), index=True)
    reminder_id: Mapped[int | None] = mapped_column(ForeignKey("reminders.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(40), default="in_app")
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[NotificationStatus] = mapped_column(
        enum_type(NotificationStatus, "notification_status"), default=NotificationStatus.DELIVERED
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    reason_code: Mapped[str] = mapped_column(String(80), index=True)
    reason: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[EscalationStatus] = mapped_column(
        enum_type(EscalationStatus, "escalation_status"), default=EscalationStatus.OPEN, index=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="escalations")
    reviewer: Mapped[User | None] = relationship()


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(80), index=True)
    action_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"), default=ApprovalStatus.PENDING, index=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="approvals")
    reviewer: Mapped[User | None] = relationship()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    actor_role: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(80), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )

    actor: Mapped[User | None] = relationship()
