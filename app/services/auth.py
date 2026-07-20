from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.security import normalize_email, verify_password


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if not user or not user.active or not verify_password(password, user.password_hash):
        return None
    return user
