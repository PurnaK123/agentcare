import uuid

import pytest
from sqlalchemy import select

from app.agents.client import AgentProviderError
from app.models import (
    AgentStep,
    AgentStepStatus,
    AppointmentSlot,
    AuditEvent,
    PatientProfile,
    PatientRequest,
    Role,
    SlotStatus,
    User,
    WorkflowRun,
    WorkflowStatus,
)
from app.orchestrator import WorkflowCoordinator
from app.services.appointments import SlotUnavailableError, book_slot
from app.services.audit import record_audit
from app.tools import ToolGateway, ToolPermissionError


class FailingLLM:
    model_name = "failing-test-provider"

    def generate(self, **kwargs):
        raise AgentProviderError("Injected provider outage")


def get_patient(db):
    user = db.scalar(select(User).where(User.role == Role.PATIENT))
    patient = db.scalar(select(PatientProfile).where(PatientProfile.user_id == user.id))
    return patient, user


def make_run(db, patient_id: int, text: str) -> WorkflowRun:
    patient_request = PatientRequest(patient_id=patient_id, request_text=text)
    db.add(patient_request)
    db.flush()
    run = WorkflowRun(
        request_id=patient_request.id,
        patient_id=patient_id,
        correlation_id=str(uuid.uuid4()),
        current_step="appointment",
        state={},
    )
    db.add(run)
    db.flush()
    return run


def test_tool_gateway_denies_cross_agent_operational_tool(db):
    patient, _ = get_patient(db)
    run = make_run(db, patient.id, "Synthetic test request")
    gateway = ToolGateway(db, run, "coordinator")

    with pytest.raises(ToolPermissionError):
        gateway.active_appointments()


def test_database_slot_state_prevents_second_booking(db):
    patient, _ = get_patient(db)
    run_one = make_run(db, patient.id, "First synthetic booking")
    run_two = make_run(db, patient.id, "Second synthetic booking")
    slot = db.scalar(
        select(AppointmentSlot)
        .where(AppointmentSlot.status == SlotStatus.AVAILABLE)
        .order_by(AppointmentSlot.start_time)
    )
    book_slot(
        db,
        patient_id=patient.id,
        slot_id=slot.id,
        reason="First synthetic booking",
        workflow_run_id=run_one.id,
        correlation_id=run_one.correlation_id,
    )
    db.commit()

    with pytest.raises(SlotUnavailableError):
        book_slot(
            db,
            patient_id=patient.id,
            slot_id=slot.id,
            reason="Conflicting synthetic booking",
            workflow_run_id=run_two.id,
            correlation_id=run_two.correlation_id,
        )


def test_provider_failure_persists_retry_checkpoint_and_failed_step(db):
    patient, user = get_patient(db)
    run = WorkflowCoordinator(db, FailingLLM(), user).submit(
        patient=patient,
        request_text="Book a synthetic Cardiology appointment next week.",
    )

    assert run.status == WorkflowStatus.RETRY_PENDING
    assert run.current_step == "safety"
    failed = db.scalar(
        select(AgentStep).where(
            AgentStep.workflow_run_id == run.id,
            AgentStep.status == AgentStepStatus.FAILED,
        )
    )
    assert failed is not None
    assert failed.agent_name == "safety"
    assert failed.error == "AgentProviderError"


def test_audit_events_are_append_only(db):
    event = record_audit(
        db,
        action="synthetic.test",
        entity_type="test",
        entity_id="1",
        actor_role="test",
    )
    db.commit()
    event.action = "tampered"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    persisted = db.get(AuditEvent, event.id)
    assert persisted.action == "synthetic.test"
