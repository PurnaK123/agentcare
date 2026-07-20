from app.database import SessionLocal
from app.services.follow_up import process_due_reminders


def main() -> None:
    with SessionLocal() as db:
        delivered = process_due_reminders(db)
    print(f"Delivered {delivered} due in-app reminder(s).")


if __name__ == "__main__":
    main()
