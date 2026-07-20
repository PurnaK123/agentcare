import hashlib
import hmac
import json
import re
import secrets
from typing import Any

from fastapi import HTTPException, Request, status
from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf_token(request: Request, supplied_token: str | None) -> None:
    expected_token = request.session.get("csrf_token", "")
    if not supplied_token or not hmac.compare_digest(expected_token, supplied_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "document_content",
    "raw_text",
)


def redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(part in str(key).casefold() for part in SENSITIVE_KEY_PARTS)
            else redact_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_metadata(item) for item in value]
    if isinstance(value, str) and len(value) > 1000:
        return f"{value[:997]}..."
    return value


def redact_sensitive_text(text: str) -> str:
    patterns = (
        (r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[EMAIL]"),
        (r"(?<!\d)(?:\+91[-\s]?)?[6-9]\d{9}(?!\d)", "[PHONE]"),
        (r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)", "[IDENTIFIER]"),
    )
    redacted = text
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    return redacted
