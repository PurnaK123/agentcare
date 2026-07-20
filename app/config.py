from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "AgentCare"
    base_url: str = "http://127.0.0.1:8000"
    database_url: str = "sqlite:///./var/agentcare.db"
    auto_create_tables: bool = True

    session_secret: str = "local-development-secret-change-before-deploying"
    cookie_secure: bool = False
    session_max_age_seconds: int = 28_800

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: int = 30
    max_agent_retries: int = 3
    max_workflow_steps: int = 12

    upload_dir: Path = Path("./var/uploads")
    staging_dir: Path = Path("./var/staging")
    max_upload_mb: int = 8
    timezone: str = "Asia/Kolkata"
    emergency_number: str = "112"
    demo_mode: bool = True
    seed_demo_data: bool = True

    demo_patient_email: str = "asha.patient@example.test"
    demo_patient_password: str = "change-me-patient"
    demo_staff_email: str = "meera.staff@example.test"
    demo_staff_password: str = "change-me-staff"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("max_upload_mb")
    @classmethod
    def validate_upload_size(cls, value: int) -> int:
        if not 1 <= value <= 25:
            raise ValueError("MAX_UPLOAD_MB must be between 1 and 25")
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @field_validator("max_agent_retries")
    @classmethod
    def validate_retries(cls, value: int) -> int:
        if not 1 <= value <= 5:
            raise ValueError("MAX_AGENT_RETRIES must be between 1 and 5")
        return value

    def validate_for_startup(self) -> None:
        if self.app_env == "production":
            if self.session_secret == self.openai_api_key or self.session_secret.startswith("sk-"):
                raise RuntimeError("SESSION_SECRET must be distinct from all provider API keys")
            if len(self.session_secret) < 32 or "change" in self.session_secret.lower():
                raise RuntimeError("A strong SESSION_SECRET is required in production")
            if not self.cookie_secure:
                raise RuntimeError("COOKIE_SECURE must be true in production")
            if not self.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY is required in production")

    def resolve_data_paths(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
