from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utc_now
from app.models import (
    Appointment,
    Notification,
    NotificationStatus,
    Reminder,
    ReminderStatus,
)
from app.schemas import FollowUpPlan
from app.services.audit import record_audit


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _create_reminder(
    db: Session,
    *,
    appointment: Appointment,
    reminder_type: str,
    scheduled_at,
    message: str,
    idempotency_key: str,
) -> Reminder:
    existing = db.scalar(select(Reminder).where(Reminder.idempotency_key == idempotency_key))
    if existing:
        return existing
    reminder = Reminder(
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
        reminder_type=reminder_type,
        scheduled_at=scheduled_at,
        status=ReminderStatus.SCHEDULED,
        message=message,
        idempotency_key=idempotency_key,
    )
    db.add(reminder)
    db.flush()
    return reminder


def create_follow_up_records(
    db: Session,
    *,
    appointment: Appointment,
    plan: FollowUpPlan,
    correlation_id: str,
) -> list[Reminder]:
    reminders: list[Reminder] = []
    appointment_start = _as_utc(appointment.scheduled_start)
    appointment_end = _as_utc(appointment.scheduled_end)
    if plan.create_appointment_reminder:
        scheduled_at = appointment_start - timedelta(hours=plan.reminder_hours_before)
        if scheduled_at <= utc_now():
            scheduled_at = utc_now() + timedelta(minutes=5)
        reminders.append(
            _create_reminder(
                db,
                appointment=appointment,
                reminder_type="appointment_reminder",
                scheduled_at=scheduled_at,
                message="Upcoming appointment reminder. Sign in to view appointment details.",
                idempotency_key=f"appointment-{appointment.id}-v{appointment.version}-reminder",
            )
        )
    if plan.create_follow_up_task:
        reminders.append(
            _create_reminder(
                db,
                appointment=appointment,
                reminder_type="post_visit_follow_up",
                scheduled_at=appointment_end + timedelta(days=plan.follow_up_days_after),
                message="Post-visit administrative follow-up task.",
                idempotency_key=f"appointment-{appointment.id}-v{appointment.version}-follow-up",
            )
        )
    db.flush()
    record_audit(
        db,
        action="reminders.scheduled",
        entity_type="appointment",
        entity_id=appointment.id,
        actor_role="follow_up_agent",
        correlation_id=correlation_id,
        metadata={"reminder_ids": [reminder.id for reminder in reminders]},
    )
    return reminders


def create_confirmation_notification(
    db: Session, *, appointment: Appointment, message: str
) -> Notification:
    idempotency_key = f"appointment-{appointment.id}-v{appointment.version}-confirmation"
    existing = db.scalar(
        select(Notification).where(Notification.idempotency_key == idempotency_key)
    )
    if existing:
        return existing
    notification = Notification(
        patient_id=appointment.patient_id,
        channel="in_app",
        message=message,
        status=NotificationStatus.DELIVERED,
        idempotency_key=idempotency_key,
        delivered_at=utc_now(),
    )
    db.add(notification)
    db.flush()
    return notification


def process_due_reminders(db: Session, limit: int = 100) -> int:
    due = list(
        db.scalars(
            select(Reminder)
            .where(
                Reminder.status == ReminderStatus.SCHEDULED,
                Reminder.scheduled_at <= utc_now(),
            )
            .order_by(Reminder.scheduled_at)
            .limit(limit)
            .with_for_update()
        )
    )
    delivered = 0
    for reminder in due:
        key = f"reminder-{reminder.id}-delivery"
        notification = db.scalar(
            select(Notification).where(Notification.idempotency_key == key)
        )
        if not notification:
            db.add(
                Notification(
                    patient_id=reminder.patient_id,
                    reminder_id=reminder.id,
                    channel="in_app",
                    message=reminder.message,
                    status=NotificationStatus.DELIVERED,
                    idempotency_key=key,
                    delivered_at=utc_now(),
                )
            )
        reminder.status = ReminderStatus.SENT
        delivered += 1
    db.commit()
    return delivered
