from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import (
    AppointmentSlot,
    Department,
    Doctor,
    PatientProfile,
    Role,
    SlotStatus,
    User,
)
from app.security import hash_password, normalize_email

DEPARTMENTS = (
    {
        "name": "Cardiology",
        "slug": "cardiology",
        "description": "Administrative coordination for heart and cardiovascular care.",
        "required_document_types": ["ecg", "blood_report"],
        "doctors": ("Dr. Kavya Rao", "Dr. Arjun Mehta"),
    },
    {
        "name": "General Medicine",
        "slug": "general-medicine",
        "description": "General outpatient administrative coordination and referrals.",
        "required_document_types": [],
        "doctors": ("Dr. Nisha Iyer",),
    },
    {
        "name": "Orthopaedics",
        "slug": "orthopaedics",
        "description": "Administrative coordination for bone, joint, and mobility care.",
        "required_document_types": ["imaging_report"],
        "doctors": ("Dr. Rohan Das",),
    },
    {
        "name": "Neurology",
        "slug": "neurology",
        "description": "Administrative coordination for neurology appointments and records.",
        "required_document_types": ["referral_letter"],
        "doctors": ("Dr. Sana Kulkarni",),
    },
)


def create_user(
    db: Session, *, name: str, email: str, password: str, role: Role
) -> tuple[User, bool]:
    normalized_email = normalize_email(email)
    existing = db.scalar(select(User).where(User.email == normalized_email))
    if existing:
        # Demo credentials are configured through .env and must stay usable after a restart.
        existing.name = name
        existing.password_hash = hash_password(password)
        existing.role = role
        existing.active = True
        db.flush()
        return existing, False
    user = User(
        name=name,
        email=normalized_email,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.flush()
    return user, True


def seed_database(db: Session) -> None:
    settings = get_settings()
    patient_user, patient_created = create_user(
        db,
        name="Asha Nair (Synthetic)",
        email=settings.demo_patient_email,
        password=settings.demo_patient_password,
        role=Role.PATIENT,
    )
    create_user(
        db,
        name="Meera Shah (Synthetic Staff)",
        email=settings.demo_staff_email,
        password=settings.demo_staff_password,
        role=Role.STAFF,
    )
    if patient_created or not patient_user.patient_profile:
        db.add(
            PatientProfile(
                user_id=patient_user.id,
                synthetic_reference="SYN-PAT-0001",
                date_of_birth=date(1988, 4, 16),
                phone="SYNTHETIC-NUMBER",
                preferred_language="English",
                emergency_contact="Synthetic contact only",
                demo_data_acknowledged=True,
            )
        )

    doctors: list[Doctor] = []
    for department_data in DEPARTMENTS:
        department = db.scalar(
            select(Department).where(Department.slug == department_data["slug"])
        )
        if not department:
            department = Department(
                name=department_data["name"],
                slug=department_data["slug"],
                description=department_data["description"],
                required_document_types=department_data["required_document_types"],
            )
            db.add(department)
            db.flush()
        for index, doctor_name in enumerate(department_data["doctors"], start=1):
            doctor = db.scalar(
                select(Doctor).where(
                    Doctor.department_id == department.id,
                    Doctor.name == doctor_name,
                )
            )
            if not doctor:
                doctor = Doctor(
                    department_id=department.id,
                    name=doctor_name,
                    registration_reference=f"SYN-{department.slug[:4].upper()}-{index:03d}",
                )
                db.add(doctor)
                db.flush()
            doctors.append(doctor)

    local_zone = ZoneInfo(settings.timezone)
    today = datetime.now(local_zone).date()
    appointment_times = (time(9, 30), time(11, 0), time(14, 30))
    for doctor_index, doctor in enumerate(doctors):
        for day_offset in range(1, 22):
            slot_date = today + timedelta(days=day_offset)
            if slot_date.weekday() >= 5:
                continue
            slot_time = appointment_times[(day_offset + doctor_index) % len(appointment_times)]
            local_start = datetime.combine(slot_date, slot_time, tzinfo=local_zone)
            start_time = local_start.astimezone(UTC)
            exists = db.scalar(
                select(AppointmentSlot.id).where(
                    AppointmentSlot.doctor_id == doctor.id,
                    AppointmentSlot.start_time == start_time,
                )
            )
            if not exists:
                db.add(
                    AppointmentSlot(
                        doctor_id=doctor.id,
                        start_time=start_time,
                        end_time=start_time + timedelta(minutes=30),
                        status=SlotStatus.AVAILABLE,
                    )
                )
    db.commit()


def main() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_database(db)
    print("Synthetic AgentCare data is ready.")


if __name__ == "__main__":
    main()
