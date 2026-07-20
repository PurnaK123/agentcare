import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import Role, User
from app.security import verify_password
from app.seed import create_user


def production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "session_secret": "independent-session-secret-with-more-than-32-characters",
        "cookie_secure": True,
        "openai_api_key": "sk-test-provider-key",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_rejects_provider_key_as_session_secret():
    settings = production_settings(
        session_secret="sk-test-provider-key",
        openai_api_key="sk-test-provider-key",
    )
    with pytest.raises(RuntimeError, match="distinct"):
        settings.validate_for_startup()


def test_production_accepts_independent_secrets():
    production_settings().validate_for_startup()


def test_seeding_refreshes_configured_synthetic_account_password(db):
    user = db.scalar(select(User).where(User.role == Role.PATIENT))
    refreshed, created = create_user(
        db,
        name="Updated Synthetic Patient",
        email=user.email,
        password="new-synthetic-demo-password",
        role=Role.PATIENT,
    )

    assert not created
    assert refreshed.id == user.id
    assert refreshed.name == "Updated Synthetic Patient"
    assert verify_password("new-synthetic-demo-password", refreshed.password_hash)
