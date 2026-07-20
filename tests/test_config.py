import pytest

from app.config import Settings


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
