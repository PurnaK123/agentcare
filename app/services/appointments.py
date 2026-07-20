from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import (
    Appointment,
    AppointmentSlot,
    AppointmentStatus,
    Department,
    Doctor,
    PatientProfile,
    Reminder,
    ReminderStatus,
    SlotStatus,
)
from app.services.audit import record_audit


class AppointmentError(ValueError):
    pass


class SlotUnavailableError(AppointmentError):
    pass


def date_window_to_utc(
    start_date: date | None, end_date: date | None
) -> tuple[datetime, datetime]:
    zone = ZoneInfo(get_settings().timezone)
    local_today = datetime.now(zone).date()
    start_date = start_date or local_today
    end_date = end_date or (start_date + timedelta(days=14))
    if end_date < start_date:
        raise AppointmentError("The end date cannot be before the start date")
    if (end_date - start_date).days > 60:
        end_date = start_date + timedelta(days=60)
    local_start = datetime.combine(start_date, time.min, tzinfo=zone)
    local_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def list_available_slots(
    db: Session,
    *,
    department_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 12,
) -> list[AppointmentSlot]:
    start_at, end_at = date_window_to_utc(start_date, end_date)
    statement = (
        select(AppointmentSlot)
        .join(AppointmentSlot.doctor)
        .join(Doctor.department)
        .options(joinedload(AppointmentSlot.doctor).joinedload(Doctor.department))
        .where(
            Department.id == department_id,
            Department.active.is_(True),
            Doctor.active.is_(True),
            AppointmentSlot.status == SlotStatus.AVAILABLE,
            AppointmentSlot.start_time >= start_at,
            AppointmentSlot.start_time < end_at,
        )
        .order_by(AppointmentSlot.start_time)
        .limit(min(max(limit, 1), 30))
    )
    return list(db.scalars(statement))


def list_active_appointments(db: Session, patient_id: int) -> list[Appointment]:
    statement = (
        select(Appointment)
        .options(joinedload(Appointment.doctor).joinedload(Doctor.department))
        .where(
            Appointment.patient_id == patient_id,
            Appointment.status.in_([AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED]),
        )
        .order_by(Appointment.scheduled_start)
    )
    return list(db.scalars(statement))


def book_slot(
    db: Session,
    *,
    patient_id: int,
    slot_id: int,
    reason: str,
    workflow_run_id: int,
    correlation_id: str,
) -> Appointment:
    if not db.get(PatientProfile, patient_id):
        raise AppointmentError("Patient does not exist")
    existing = db.scalar(
        select(Appointment).where(
            Appointment.workflow_run_id == workflow_run_id,
            Appointment.status.in_([AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED]),
        )
    )
    if existing:
        return existing
    slot = db.scalar(
        select(AppointmentSlot)
        .options(joinedload(AppointmentSlot.doctor))
        .where(AppointmentSlot.id == slot_id)
        .with_for_update()
    )
    if not slot or slot.status != SlotStatus.AVAILABLE or not slot.doctor.active:
        raise SlotUnavailableError("The selected slot is no longer available")
    appointment = Appointment(
        patient_id=patient_id,
        doctor_id=slot.doctor_id,
        slot_id=slot.id,
        workflow_run_id=workflow_run_id,
        status=AppointmentStatus.CONFIRMED,
        reason=reason[:1000],
        scheduled_start=slot.start_time,
        scheduled_end=slot.end_time,
    )
    slot.status = SlotStatus.BOOKED
    slot.version += 1
    db.add(appointment)
    try:
        db.flush()
    except IntegrityError as exc:
        raise SlotUnavailableError("The selected slot was booked concurrently") from exc
    record_audit(
        db,
        action="appointment.booked",
        entity_type="appointment",
        entity_id=appointment.id,
        actor_role="appointment_agent",
        correlation_id=correlation_id,
        metadata={"slot_id": slot.id, "patient_id": patient_id},
    )
    return appointment


def reschedule_appointment(
    db: Session,
    *,
    patient_id: int,
    appointment_id: int,
    new_slot_id: int,
    correlation_id: str,
) -> Appointment:
    appointment = db.scalar(
        select(Appointment)
        .where(Appointment.id == appointment_id, Appointment.patient_id == patient_id)
        .with_for_update()
    )
    if not appointment or appointment.status not in {
        AppointmentStatus.PENDING,
        AppointmentStatus.CONFIRMED,
    }:
        raise AppointmentError("Active appointment not found")
    if appointment.slot_id == new_slot_id:
        return appointment
    new_slot = db.scalar(
        select(AppointmentSlot).where(AppointmentSlot.id == new_slot_id).with_for_update()
    )
    if not new_slot or new_slot.status != SlotStatus.AVAILABLE:
        raise SlotUnavailableError("The new slot is no longer available")
    old_slot_id = appointment.slot_id
    if old_slot_id:
        old_slot = db.get(AppointmentSlot, old_slot_id)
        if old_slot:
            old_slot.status = SlotStatus.AVAILABLE
            old_slot.version += 1
    new_slot.status = SlotStatus.BOOKED
    new_slot.version += 1
    appointment.slot_id = new_slot.id
    appointment.doctor_id = new_slot.doctor_id
    appointment.scheduled_start = new_slot.start_time
    appointment.scheduled_end = new_slot.end_time
    appointment.version += 1
    db.execute(
        select(Reminder).where(Reminder.appointment_id == appointment.id).with_for_update()
    )
    for reminder in appointment.reminders:
        if reminder.status == ReminderStatus.SCHEDULED:
            reminder.status = ReminderStatus.CANCELLED
    db.flush()
    record_audit(
        db,
        action="appointment.rescheduled",
        entity_type="appointment",
        entity_id=appointment.id,
        actor_role="appointment_agent",
        correlation_id=correlation_id,
        metadata={"old_slot_id": old_slot_id, "new_slot_id": new_slot.id},
    )
    return appointment


def cancel_appointment(
    db: Session,
    *,
    patient_id: int,
    appointment_id: int,
    reason: str,
    correlation_id: str,
) -> Appointment:
    appointment = db.scalar(
        select(Appointment)
        .where(Appointment.id == appointment_id, Appointment.patient_id == patient_id)
        .with_for_update()
    )
    if not appointment:
        raise AppointmentError("Appointment not found")
    if appointment.status == AppointmentStatus.CANCELLED:
        return appointment
    if appointment.status not in {AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED}:
        raise AppointmentError("This appointment can no longer be cancelled")
    old_slot_id = appointment.slot_id
    if old_slot_id:
        slot = db.get(AppointmentSlot, old_slot_id)
        if slot:
            slot.status = SlotStatus.AVAILABLE
            slot.version += 1
    appointment.slot_id = None
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancellation_reason = reason[:500]
    appointment.version += 1
    for reminder in appointment.reminders:
        if reminder.status == ReminderStatus.SCHEDULED:
            reminder.status = ReminderStatus.CANCELLED
    db.flush()
    record_audit(
        db,
        action="appointment.cancelled",
        entity_type="appointment",
        entity_id=appointment.id,
        actor_role="appointment_agent",
        correlation_id=correlation_id,
        metadata={"released_slot_id": old_slot_id},
    )
    return appointment
