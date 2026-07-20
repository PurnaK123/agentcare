from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import AuditEvent, User
from app.security import redact_metadata


@event.listens_for(Session, "before_flush")
def prevent_audit_mutation(session: Session, flush_context, instances) -> None:
    for item in session.deleted:
        if isinstance(item, AuditEvent):
            raise ValueError("Audit events are append-only")
    for item in session.dirty:
        if isinstance(item, AuditEvent) and session.is_modified(item, include_collections=False):
            raise ValueError("Audit events are append-only")


def record_audit(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int | str,
    actor: User | None = None,
    actor_role: str | None = None,
    correlation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor.id if actor else None,
        actor_role=actor.role.value if actor else (actor_role or "system"),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        correlation_id=correlation_id,
        event_metadata=redact_metadata(metadata or {}),
    )
    db.add(event)
    return event
