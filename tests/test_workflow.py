from sqlalchemy import select

from app.models import (
    AgentStep,
    Appointment,
    ApprovalRequest,
    DocumentStatus,
    Escalation,
    PatientDocument,
    PatientProfile,
    Reminder,
    RequestStatus,
    Role,
    User,
    WorkflowStatus,
)
from app.orchestrator import WorkflowCoordinator
from app.services.oversight import review_approval
from tests.fakes import FakeLLM


def patient_and_staff(db):
    patient_user = db.scalar(select(User).where(User.role == Role.PATIENT))
    staff_user = db.scalar(select(User).where(User.role == Role.STAFF))
    patient = db.scalar(
        select(PatientProfile).where(PatientProfile.user_id == patient_user.id)
    )
    return patient, patient_user, staff_user


def test_booking_workflow_persists_agent_tools_and_results(db, monkeypatch):
    patient, patient_user, _ = patient_and_staff(db)
    llm = FakeLLM()
    coordinator = WorkflowCoordinator(db, llm, patient_user)
    graph = coordinator._build_workflow_graph()
    assert {
        "intake",
        "safety",
        "routing",
        "approval",
        "appointment",
        "documents",
        "follow_up",
        "confirmation",
    } <= set(graph.nodes)

    graph_builds = 0
    original_build = coordinator._build_workflow_graph

    def build_graph():
        nonlocal graph_builds
        graph_builds += 1
        return original_build()

    monkeypatch.setattr(coordinator, "_build_workflow_graph", build_graph)
    run = coordinator.submit(
        patient=patient,
        request_text="I need a Cardiology appointment next week.",
        uploads=[("previous-ecg.txt", b"Synthetic ECG report for workflow testing only.")],
    )

    assert graph_builds == 1
    assert run.status == WorkflowStatus.COMPLETED, (run.current_step, run.last_error)
    assert run.request.status == RequestStatus.COMPLETED
    assert run.request.confirmation_message.startswith("Request REQ-")
    assert db.scalar(select(Appointment).where(Appointment.workflow_run_id == run.id))
    assert len(list(db.scalars(select(Reminder)))) == 2
    document = db.scalar(select(PatientDocument))
    assert document.document_type == "ecg"
    assert document.status == DocumentStatus.CLASSIFIED

    steps = list(
        db.scalars(
            select(AgentStep)
            .where(AgentStep.workflow_run_id == run.id)
            .order_by(AgentStep.id)
        )
    )
    assert {step.agent_name for step in steps} == {
        "coordinator",
        "safety",
        "department_routing",
        "appointment",
        "document",
        "follow_up",
    }
    assert any(call["tool"] == "book_appointment" for step in steps for call in step.tool_calls)
    assert run.state["documents"]["missing_document_types"] == ["blood_report"]


def test_emergency_request_is_blocked_and_escalated_without_booking(db):
    patient, patient_user, _ = patient_and_staff(db)
    run = WorkflowCoordinator(db, FakeLLM(), patient_user).submit(
        patient=patient,
        request_text="I have chest pain and cannot breathe. Book me for next week.",
    )

    assert run.status == WorkflowStatus.ESCALATED
    assert "112" in run.request.confirmation_message
    assert db.scalar(select(Escalation).where(Escalation.workflow_run_id == run.id))
    assert not db.scalar(select(Appointment).where(Appointment.workflow_run_id == run.id))


def test_exact_duplicate_document_is_recorded_not_stored_twice(db):
    patient, patient_user, _ = patient_and_staff(db)
    content = b"Synthetic ECG document. No real patient data."
    run = WorkflowCoordinator(db, FakeLLM(), patient_user).submit(
        patient=patient,
        request_text="Please coordinate this Cardiology document only.",
        uploads=[("ecg-one.txt", content), ("renamed-ecg.txt", content)],
    )

    documents = list(
        db.scalars(
            select(PatientDocument)
            .where(PatientDocument.request_id == run.request_id)
            .order_by(PatientDocument.id)
        )
    )
    assert len(documents) == 2
    assert documents[1].status == DocumentStatus.DUPLICATE
    assert documents[1].duplicate_of_id == documents[0].id
    assert documents[0].storage_reference == documents[1].storage_reference


def test_low_confidence_route_requires_exact_staff_approval(db):
    patient, patient_user, staff_user = patient_and_staff(db)
    llm = FakeLLM(routing_confidence=0.6)
    coordinator = WorkflowCoordinator(db, llm, patient_user)
    run = coordinator.submit(
        patient=patient,
        request_text="I need a Cardiology appointment but am unsure.",
    )

    assert run.status == WorkflowStatus.WAITING_FOR_APPROVAL
    approval = db.scalar(
        select(ApprovalRequest).where(ApprovalRequest.workflow_run_id == run.id)
    )
    assert approval is not None
    assert not db.scalar(select(Appointment).where(Appointment.workflow_run_id == run.id))

    review_approval(
        db,
        approval_id=approval.id,
        reviewer=staff_user,
        approve=True,
        notes="Synthetic routing reviewed.",
    )
    db.commit()
    resumed = coordinator.execute(run.id)

    assert resumed.status == WorkflowStatus.COMPLETED, (
        resumed.current_step,
        resumed.last_error,
    )
    assert db.scalar(select(Appointment).where(Appointment.workflow_run_id == run.id))
