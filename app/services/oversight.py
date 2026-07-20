from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utc_now
from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    Escalation,
    EscalationStatus,
    User,
    WorkflowRun,
)
from app.security import payload_hash
from app.services.audit import record_audit


def create_escalation(
    db: Session,
    *,
    workflow_run: WorkflowRun,
    reason_code: str,
    reason: str,
    severity: str,
) -> Escalation:
    existing = db.scalar(
        select(Escalation).where(
            Escalation.workflow_run_id == workflow_run.id,
            Escalation.reason_code == reason_code,
            Escalation.status == EscalationStatus.OPEN,
        )
    )
    if existing:
        return existing
    escalation = Escalation(
        workflow_run_id=workflow_run.id,
        reason_code=reason_code[:80],
        reason=reason[:1000],
        severity=severity,
    )
    db.add(escalation)
    db.flush()
    record_audit(
        db,
        action="escalation.created",
        entity_type="escalation",
        entity_id=escalation.id,
        actor_role="safety_agent",
        correlation_id=workflow_run.correlation_id,
        metadata={"reason_code": reason_code, "severity": severity},
    )
    return escalation


def create_approval(
    db: Session,
    *,
    workflow_run: WorkflowRun,
    action_type: str,
    action_payload: dict[str, Any],
) -> ApprovalRequest:
    digest = payload_hash(action_payload)
    existing = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.workflow_run_id == workflow_run.id,
            ApprovalRequest.action_type == action_type,
            ApprovalRequest.payload_hash == digest,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
    )
    if existing:
        return existing
    approval = ApprovalRequest(
        workflow_run_id=workflow_run.id,
        action_type=action_type[:80],
        action_payload=action_payload,
        payload_hash=digest,
    )
    db.add(approval)
    db.flush()
    record_audit(
        db,
        action="approval.requested",
        entity_type="approval_request",
        entity_id=approval.id,
        actor_role="coordinator_agent",
        correlation_id=workflow_run.correlation_id,
        metadata={"action_type": action_type, "payload_hash": digest},
    )
    return approval


def review_approval(
    db: Session,
    *,
    approval_id: int,
    reviewer: User,
    approve: bool,
    notes: str,
) -> ApprovalRequest:
    approval = db.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id)
        .with_for_update()
    )
    if not approval:
        raise ValueError("Approval request not found")
    if approval.status != ApprovalStatus.PENDING:
        raise ValueError("Approval request has already been reviewed")
    if payload_hash(approval.action_payload) != approval.payload_hash:
        approval.status = ApprovalStatus.EXPIRED
        db.flush()
        raise ValueError("Approval payload changed and can no longer be approved")
    approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    approval.reviewed_by = reviewer.id
    approval.reviewed_at = utc_now()
    approval.review_notes = notes[:1000]
    record_audit(
        db,
        action="approval.approved" if approve else "approval.rejected",
        entity_type="approval_request",
        entity_id=approval.id,
        actor=reviewer,
        correlation_id=approval.workflow_run.correlation_id,
        metadata={"payload_hash": approval.payload_hash},
    )
    db.flush()
    return approval


def review_escalation(
    db: Session,
    *,
    escalation_id: int,
    reviewer: User,
    resolve: bool,
    notes: str,
) -> Escalation:
    escalation = db.scalar(
        select(Escalation).where(Escalation.id == escalation_id).with_for_update()
    )
    if not escalation:
        raise ValueError("Escalation not found")
    if escalation.status != EscalationStatus.OPEN:
        raise ValueError("Escalation has already been reviewed")
    escalation.status = EscalationStatus.RESOLVED if resolve else EscalationStatus.DISMISSED
    escalation.reviewed_by = reviewer.id
    escalation.reviewed_at = utc_now()
    escalation.review_notes = notes[:1000]
    record_audit(
        db,
        action="escalation.resolved" if resolve else "escalation.dismissed",
        entity_type="escalation",
        entity_id=escalation.id,
        actor=reviewer,
        correlation_id=escalation.workflow_run.correlation_id,
    )
    db.flush()
    return escalation
