import re
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.agents import OpenAIJsonClient
from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user, require_patient, require_staff
from app.models import (
    Appointment,
    AppointmentSlot,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    Department,
    Doctor,
    DocumentStatus,
    Escalation,
    EscalationStatus,
    Notification,
    PatientDocument,
    PatientProfile,
    PatientRequest,
    Reminder,
    Role,
    SlotStatus,
    User,
    WorkflowRun,
    WorkflowStatus,
)
from app.orchestrator import WorkflowCoordinator
from app.presentation import flash, render
from app.security import ensure_csrf_token, normalize_email, validate_csrf_token
from app.services.appointments import list_available_slots
from app.services.audit import record_audit
from app.services.auth import authenticate_user
from app.services.documents import DocumentError, resolve_document_path
from app.services.oversight import review_approval, review_escalation

router = APIRouter()


def _patient_profile(user: User) -> PatientProfile:
    if not user.patient_profile:
        raise HTTPException(status_code=409, detail="Patient profile is not configured")
    return user.patient_profile


def _owned_request(db: Session, request_id: int, patient_id: int) -> PatientRequest:
    result = db.execute(
        select(PatientRequest)
        .options(
            joinedload(PatientRequest.workflow_run).joinedload(WorkflowRun.steps),
            joinedload(PatientRequest.documents),
        )
        .where(PatientRequest.id == request_id, PatientRequest.patient_id == patient_id)
    ).unique()
    patient_request = result.scalar_one_or_none()
    if not patient_request:
        raise HTTPException(status_code=404, detail="Request not found")
    return patient_request


def _request_detail_context(db: Session, patient_request: PatientRequest) -> dict:
    run = patient_request.workflow_run
    appointment = None
    reminders: list[Reminder] = []
    escalations: list[Escalation] = []
    approvals: list[ApprovalRequest] = []
    if run:
        appointment_id = (run.state or {}).get("appointment", {}).get("appointment_id")
        if appointment_id:
            appointment = db.scalar(
                select(Appointment)
                .options(joinedload(Appointment.doctor).joinedload(Doctor.department))
                .where(Appointment.id == appointment_id)
            )
            if appointment:
                reminders = list(
                    db.scalars(
                        select(Reminder)
                        .where(Reminder.appointment_id == appointment.id)
                        .order_by(Reminder.scheduled_at)
                    )
                )
        escalations = list(
            db.scalars(select(Escalation).where(Escalation.workflow_run_id == run.id))
        )
        approvals = list(
            db.scalars(select(ApprovalRequest).where(ApprovalRequest.workflow_run_id == run.id))
        )
    return {
        "patient_request": patient_request,
        "workflow": run,
        "appointment": appointment,
        "reminders": reminders,
        "escalations": escalations,
        "approvals": approvals,
    }


@router.get("/login", name="login")
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html", {"title": "Sign in"})


@router.post("/login", name="login_submit")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    user = authenticate_user(db, email, password)
    if not user:
        record_audit(
            db,
            action="auth.login_failed",
            entity_type="session",
            entity_id="anonymous",
            actor_role="anonymous",
            metadata={"email_fingerprint": str(hash(normalize_email(email)))},
        )
        db.commit()
        return render(
            request,
            "login.html",
            {"title": "Sign in", "error": "Email or password is incorrect."},
            status_code=400,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    ensure_csrf_token(request)
    record_audit(
        db,
        action="auth.login_succeeded",
        entity_type="user",
        entity_id=user.id,
        actor=user,
    )
    db.commit()
    destination = "/patient" if user.role == Role.PATIENT else "/staff"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout", name="logout")
def logout(
    request: Request,
    csrf_token: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    record_audit(
        db,
        action="auth.logout",
        entity_type="user",
        entity_id=user.id,
        actor=user,
    )
    db.commit()
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/patient", name="patient_dashboard")
def patient_dashboard(
    request: Request,
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    patient = _patient_profile(user)
    requests = list(
        db.scalars(
            select(PatientRequest)
            .where(PatientRequest.patient_id == patient.id)
            .order_by(PatientRequest.created_at.desc())
            .limit(10)
        )
    )
    appointments = list(
        db.scalars(
            select(Appointment)
            .options(joinedload(Appointment.doctor).joinedload(Doctor.department))
            .where(Appointment.patient_id == patient.id)
            .order_by(Appointment.scheduled_start.desc())
            .limit(8)
        )
    )
    documents = list(
        db.scalars(
            select(PatientDocument)
            .where(PatientDocument.patient_id == patient.id)
            .order_by(PatientDocument.created_at.desc())
            .limit(8)
        )
    )
    reminders = list(
        db.scalars(
            select(Reminder)
            .where(Reminder.patient_id == patient.id)
            .order_by(Reminder.scheduled_at)
            .limit(8)
        )
    )
    notifications = list(
        db.scalars(
            select(Notification)
            .where(Notification.patient_id == patient.id)
            .order_by(Notification.created_at.desc())
            .limit(5)
        )
    )
    return render(
        request,
        "patient/dashboard.html",
        {
            "title": "Patient home",
            "user": user,
            "patient": patient,
            "requests": requests,
            "appointments": appointments,
            "documents": documents,
            "reminders": reminders,
            "notifications": notifications,
        },
    )


@router.get("/patient/profile", name="patient_profile")
def patient_profile_page(request: Request, user: User = Depends(require_patient)):
    return render(
        request,
        "patient/profile.html",
        {"title": "Synthetic profile", "user": user, "patient": _patient_profile(user)},
    )


@router.post("/patient/profile", name="patient_profile_update")
def patient_profile_update(
    request: Request,
    name: str = Form(...),
    date_of_birth: str = Form(""),
    phone: str = Form(""),
    preferred_language: str = Form("English"),
    emergency_contact: str = Form(""),
    demo_acknowledgement: str | None = Form(None),
    csrf_token: str = Form(...),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    if not demo_acknowledgement:
        flash(request, "Confirm that all entered information is synthetic.", "error")
        return RedirectResponse("/patient/profile", status_code=303)
    clean_name = " ".join(name.split())[:120]
    if len(clean_name) < 2:
        flash(request, "Enter a synthetic display name.", "error")
        return RedirectResponse("/patient/profile", status_code=303)
    patient = _patient_profile(user)
    user.name = clean_name
    try:
        patient.date_of_birth = date.fromisoformat(date_of_birth) if date_of_birth else None
    except ValueError:
        flash(request, "Enter a valid synthetic date of birth.", "error")
        return RedirectResponse("/patient/profile", status_code=303)
    patient.phone = phone.strip()[:30] or None
    patient.preferred_language = preferred_language.strip()[:40] or "English"
    patient.emergency_contact = emergency_contact.strip()[:160] or None
    patient.demo_data_acknowledged = True
    record_audit(
        db,
        action="patient_profile.updated",
        entity_type="patient_profile",
        entity_id=patient.id,
        actor=user,
        metadata={"changed_fields": ["name", "date_of_birth", "phone", "language"]},
    )
    db.commit()
    flash(request, "Synthetic profile updated.", "success")
    return RedirectResponse("/patient/profile", status_code=303)


@router.get("/patient/requests/new", name="patient_request_new")
def patient_request_page(request: Request, user: User = Depends(require_patient)):
    return render(
        request,
        "patient/new_request.html",
        {"title": "New request", "user": user},
    )


@router.post("/patient/requests", name="patient_request_submit")
async def patient_request_submit(
    request: Request,
    request_text: str = Form(...),
    synthetic_acknowledgement: str | None = Form(None),
    csrf_token: str = Form(...),
    documents: list[UploadFile] | None = File(None),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    if not synthetic_acknowledgement:
        flash(request, "Confirm that the request and documents are entirely synthetic.", "error")
        return RedirectResponse("/patient/requests/new", status_code=303)
    uploads: list[tuple[str, bytes]] = []
    for uploaded in documents or []:
        if uploaded.filename:
            uploads.append((uploaded.filename, await uploaded.read()))
    try:
        run = WorkflowCoordinator(db, OpenAIJsonClient(), user).submit(
            patient=_patient_profile(user),
            request_text=request_text,
            uploads=uploads,
        )
    except (ValueError, DocumentError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/patient/requests/new", status_code=303)
    if run.status == WorkflowStatus.RETRY_PENDING:
        flash(
            request,
            "The workflow is saved and can be retried after the AI service recovers.",
            "warning",
        )
    else:
        flash(request, "Request submitted and persisted.", "success")
    return RedirectResponse(f"/patient/requests/{run.request_id}", status_code=303)


@router.get("/patient/requests/{request_id}", name="patient_request_detail")
def patient_request_detail(
    request: Request,
    request_id: int,
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    patient_request = _owned_request(db, request_id, _patient_profile(user).id)
    context = _request_detail_context(db, patient_request)
    context.update({"title": f"Request REQ-{request_id:05d}", "user": user, "staff_view": False})
    return render(request, "request_detail.html", context)


@router.post("/patient/workflows/{workflow_id}/retry", name="patient_workflow_retry")
def patient_workflow_retry(
    request: Request,
    workflow_id: int,
    csrf_token: str = Form(...),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    patient = _patient_profile(user)
    run = db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.id == workflow_id,
            WorkflowRun.patient_id == patient.id,
            WorkflowRun.status == WorkflowStatus.RETRY_PENDING,
        )
    )
    if not run:
        raise HTTPException(status_code=404, detail="Retryable workflow not found")
    WorkflowCoordinator(db, OpenAIJsonClient(), user).execute(run.id)
    flash(request, "Workflow retry completed or safely checkpointed.", "success")
    return RedirectResponse(f"/patient/requests/{run.request_id}", status_code=303)


@router.get("/patient/appointments/{appointment_id}/reschedule", name="reschedule_page")
def reschedule_page(
    request: Request,
    appointment_id: int,
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    patient = _patient_profile(user)
    appointment = db.scalar(
        select(Appointment)
        .options(joinedload(Appointment.doctor).joinedload(Doctor.department))
        .where(Appointment.id == appointment_id, Appointment.patient_id == patient.id)
    )
    if not appointment or not appointment.slot_id:
        raise HTTPException(status_code=404, detail="Active appointment not found")
    slots = list_available_slots(
        db,
        department_id=appointment.doctor.department_id,
        start_date=datetime.now(ZoneInfo(get_settings().timezone)).date(),
        end_date=datetime.now(ZoneInfo(get_settings().timezone)).date() + timedelta(days=30),
        limit=20,
    )
    return render(
        request,
        "patient/reschedule.html",
        {"title": "Reschedule", "user": user, "appointment": appointment, "slots": slots},
    )


@router.post("/patient/appointments/{appointment_id}/reschedule", name="reschedule_submit")
def reschedule_submit(
    request: Request,
    appointment_id: int,
    slot_id: int = Form(...),
    csrf_token: str = Form(...),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    patient = _patient_profile(user)
    appointment = db.scalar(
        select(Appointment)
        .options(joinedload(Appointment.doctor).joinedload(Doctor.department))
        .where(Appointment.id == appointment_id, Appointment.patient_id == patient.id)
    )
    slot = db.get(AppointmentSlot, slot_id)
    if not appointment or not slot or slot.status != SlotStatus.AVAILABLE:
        raise HTTPException(status_code=404, detail="Appointment or slot not found")
    text = (
        f"Reschedule my {appointment.doctor.department.name} appointment number "
        f"{appointment.id} to available slot number {slot.id}."
    )
    run = WorkflowCoordinator(db, OpenAIJsonClient(), user).submit(
        patient=patient, request_text=text
    )
    return RedirectResponse(f"/patient/requests/{run.request_id}", status_code=303)


@router.post("/patient/appointments/{appointment_id}/cancel", name="appointment_cancel")
def appointment_cancel(
    request: Request,
    appointment_id: int,
    csrf_token: str = Form(...),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    patient = _patient_profile(user)
    appointment = db.scalar(
        select(Appointment)
        .options(joinedload(Appointment.doctor).joinedload(Doctor.department))
        .where(Appointment.id == appointment_id, Appointment.patient_id == patient.id)
    )
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    run = WorkflowCoordinator(db, OpenAIJsonClient(), user).submit(
        patient=patient,
        request_text=(
            f"Cancel my {appointment.doctor.department.name} appointment number {appointment.id}."
        ),
    )
    return RedirectResponse(f"/patient/requests/{run.request_id}", status_code=303)


@router.get("/documents/{document_id}/download", name="document_download")
def document_download(
    document_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = db.get(PatientDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if user.role == Role.PATIENT and document.patient_id != _patient_profile(user).id:
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        path = resolve_document_path(document)
    except DocumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_audit(
        db,
        action="document.downloaded",
        entity_type="patient_document",
        entity_id=document.id,
        actor=user,
        metadata={"role": user.role.value},
    )
    db.commit()
    return FileResponse(path, media_type=document.mime_type, filename=document.original_name)


@router.get("/staff", name="staff_dashboard")
def staff_dashboard(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    recent_requests = list(
        db.scalars(
            select(PatientRequest)
            .options(joinedload(PatientRequest.patient).joinedload(PatientProfile.user))
            .order_by(PatientRequest.created_at.desc())
            .limit(20)
        )
    )
    escalations = list(
        db.scalars(
            select(Escalation)
            .options(joinedload(Escalation.workflow_run).joinedload(WorkflowRun.request))
            .where(Escalation.status == EscalationStatus.OPEN)
            .order_by(Escalation.created_at)
        )
    )
    approvals = list(
        db.scalars(
            select(ApprovalRequest)
            .options(joinedload(ApprovalRequest.workflow_run).joinedload(WorkflowRun.request))
            .where(ApprovalRequest.status == ApprovalStatus.PENDING)
            .order_by(ApprovalRequest.requested_at)
        )
    )
    counts = {
        "requests": db.scalar(select(func.count(PatientRequest.id))) or 0,
        "open_escalations": len(escalations),
        "pending_approvals": len(approvals),
        "appointments": db.scalar(select(func.count(Appointment.id))) or 0,
    }
    return render(
        request,
        "staff/dashboard.html",
        {
            "title": "Operations desk",
            "user": user,
            "requests": recent_requests,
            "escalations": escalations,
            "approvals": approvals,
            "counts": counts,
        },
    )


@router.get("/staff/requests/{request_id}", name="staff_request_detail")
def staff_request_detail(
    request: Request,
    request_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    result = db.execute(
        select(PatientRequest)
        .options(
            joinedload(PatientRequest.patient).joinedload(PatientProfile.user),
            joinedload(PatientRequest.workflow_run).joinedload(WorkflowRun.steps),
            joinedload(PatientRequest.documents),
        )
        .where(PatientRequest.id == request_id)
    ).unique()
    patient_request = result.scalar_one_or_none()
    if not patient_request:
        raise HTTPException(status_code=404, detail="Request not found")
    context = _request_detail_context(db, patient_request)
    context.update({"title": f"Request REQ-{request_id:05d}", "user": user, "staff_view": True})
    return render(request, "request_detail.html", context)


@router.post("/staff/approvals/{approval_id}", name="staff_approval_review")
def staff_approval_review(
    request: Request,
    approval_id: int,
    decision: str = Form(...),
    notes: str = Form(""),
    csrf_token: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    try:
        approval = review_approval(
            db,
            approval_id=approval_id,
            reviewer=user,
            approve=decision == "approve",
            notes=notes,
        )
        db.commit()
        WorkflowCoordinator(db, OpenAIJsonClient(), user).execute(approval.workflow_run_id)
    except ValueError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/staff", status_code=303)
    flash(request, "Approval decision persisted and workflow resumed.", "success")
    return RedirectResponse(f"/staff/requests/{approval.workflow_run.request_id}", status_code=303)


@router.post("/staff/escalations/{escalation_id}", name="staff_escalation_review")
def staff_escalation_review(
    request: Request,
    escalation_id: int,
    decision: str = Form(...),
    notes: str = Form(""),
    csrf_token: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    try:
        escalation = review_escalation(
            db,
            escalation_id=escalation_id,
            reviewer=user,
            resolve=decision == "resolve",
            notes=notes,
        )
        request_id = escalation.workflow_run.request_id
        db.commit()
    except ValueError as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/staff", status_code=303)
    flash(request, "Escalation review recorded.", "success")
    return RedirectResponse(f"/staff/requests/{request_id}", status_code=303)


@router.post("/staff/workflows/{workflow_id}/retry", name="staff_workflow_retry")
def staff_workflow_retry(
    request: Request,
    workflow_id: int,
    csrf_token: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    run = db.get(WorkflowRun, workflow_id)
    if not run or run.status != WorkflowStatus.RETRY_PENDING:
        raise HTTPException(status_code=404, detail="Retryable workflow not found")
    WorkflowCoordinator(db, OpenAIJsonClient(), user).execute(run.id)
    flash(request, "Workflow retry completed or safely checkpointed.", "success")
    return RedirectResponse(f"/staff/requests/{run.request_id}", status_code=303)


@router.post("/staff/documents/{document_id}/review", name="staff_document_review")
def staff_document_review(
    request: Request,
    document_id: int,
    document_type: str = Form(...),
    decision: str = Form(...),
    csrf_token: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    document = db.get(PatientDocument, document_id)
    allowed_types = {
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
    }
    if not document or document_type not in allowed_types:
        raise HTTPException(status_code=404, detail="Document not found")
    document.document_type = document_type
    document.status = (
        DocumentStatus.CLASSIFIED if decision == "approve" else DocumentStatus.REJECTED
    )
    document.classification_reason = "Reviewed by authorized synthetic-demo staff."
    record_audit(
        db,
        action="document.staff_reviewed",
        entity_type="patient_document",
        entity_id=document.id,
        actor=user,
        metadata={"decision": decision, "document_type": document_type},
    )
    db.commit()
    flash(request, "Document review saved.", "success")
    return RedirectResponse(f"/staff/requests/{document.request_id}", status_code=303)


@router.get("/staff/catalog", name="staff_catalog")
def staff_catalog(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    departments = list(db.scalars(select(Department).order_by(Department.name)))
    doctors = list(
        db.scalars(
            select(Doctor).options(joinedload(Doctor.department)).order_by(Doctor.name)
        )
    )
    slots = list(
        db.scalars(
            select(AppointmentSlot)
            .options(joinedload(AppointmentSlot.doctor))
            .where(AppointmentSlot.start_time >= datetime.now(UTC))
            .order_by(AppointmentSlot.start_time)
            .limit(50)
        )
    )
    return render(
        request,
        "staff/catalog.html",
        {
            "title": "Care catalog",
            "user": user,
            "departments": departments,
            "doctors": doctors,
            "slots": slots,
        },
    )


@router.post("/staff/catalog/departments", name="staff_department_add")
def staff_department_add(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    required_documents: str = Form(""),
    csrf_token: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    clean_name = " ".join(name.split())[:100]
    slug = re.sub(r"[^a-z0-9]+", "-", clean_name.casefold()).strip("-")
    department = Department(
        name=clean_name,
        slug=slug,
        description=description.strip()[:1000],
        required_document_types=[
            item.strip().casefold().replace(" ", "_")
            for item in required_documents.split(",")
            if item.strip()
        ],
    )
    db.add(department)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        flash(request, "Department name already exists.", "error")
        return RedirectResponse("/staff/catalog", status_code=303)
    record_audit(
        db,
        action="department.created",
        entity_type="department",
        entity_id=department.id,
        actor=user,
    )
    db.commit()
    flash(request, "Department created.", "success")
    return RedirectResponse("/staff/catalog", status_code=303)


@router.post("/staff/catalog/doctors", name="staff_doctor_add")
def staff_doctor_add(
    request: Request,
    department_id: int = Form(...),
    name: str = Form(...),
    csrf_token: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    if not db.get(Department, department_id):
        raise HTTPException(status_code=404, detail="Department not found")
    doctor = Doctor(
        department_id=department_id,
        name=" ".join(name.split())[:120],
        registration_reference=f"SYN-DEMO-{uuid.uuid4().hex[:8].upper()}",
    )
    db.add(doctor)
    db.flush()
    record_audit(
        db,
        action="doctor.created",
        entity_type="doctor",
        entity_id=doctor.id,
        actor=user,
    )
    db.commit()
    flash(request, "Synthetic doctor created.", "success")
    return RedirectResponse("/staff/catalog", status_code=303)


@router.post("/staff/catalog/slots", name="staff_slot_add")
def staff_slot_add(
    request: Request,
    doctor_id: int = Form(...),
    start_time: str = Form(...),
    csrf_token: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    if not db.get(Doctor, doctor_id):
        raise HTTPException(status_code=404, detail="Doctor not found")
    try:
        local_start = datetime.fromisoformat(start_time).replace(
            tzinfo=ZoneInfo(get_settings().timezone)
        )
    except ValueError:
        flash(request, "Enter a valid slot date and time.", "error")
        return RedirectResponse("/staff/catalog", status_code=303)
    start_utc = local_start.astimezone(UTC)
    if start_utc <= datetime.now(UTC):
        flash(request, "Appointment slots must be in the future.", "error")
        return RedirectResponse("/staff/catalog", status_code=303)
    slot = AppointmentSlot(
        doctor_id=doctor_id,
        start_time=start_utc,
        end_time=start_utc + timedelta(minutes=30),
    )
    db.add(slot)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        flash(request, "That doctor already has a slot at this time.", "error")
        return RedirectResponse("/staff/catalog", status_code=303)
    record_audit(
        db,
        action="appointment_slot.created",
        entity_type="appointment_slot",
        entity_id=slot.id,
        actor=user,
    )
    db.commit()
    flash(request, "Appointment slot created.", "success")
    return RedirectResponse("/staff/catalog", status_code=303)


@router.post("/staff/catalog/{entity_type}/{entity_id}/toggle", name="staff_catalog_toggle")
def staff_catalog_toggle(
    request: Request,
    entity_type: str,
    entity_id: int,
    csrf_token: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    validate_csrf_token(request, csrf_token)
    if entity_type == "department":
        entity = db.get(Department, entity_id)
        if entity:
            entity.active = not entity.active
    elif entity_type == "doctor":
        entity = db.get(Doctor, entity_id)
        if entity:
            entity.active = not entity.active
    elif entity_type == "slot":
        entity = db.get(AppointmentSlot, entity_id)
        if entity and entity.status in {SlotStatus.AVAILABLE, SlotStatus.UNAVAILABLE}:
            entity.status = (
                SlotStatus.UNAVAILABLE
                if entity.status == SlotStatus.AVAILABLE
                else SlotStatus.AVAILABLE
            )
    else:
        entity = None
    if not entity:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    record_audit(
        db,
        action=f"{entity_type}.status_toggled",
        entity_type=entity_type,
        entity_id=entity_id,
        actor=user,
    )
    db.commit()
    return RedirectResponse("/staff/catalog", status_code=303)


@router.get("/staff/audit", name="staff_audit")
def staff_audit(
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    events = list(
        db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200))
    )
    return render(
        request,
        "staff/audit.html",
        {"title": "Audit trail", "user": user, "events": events},
    )
