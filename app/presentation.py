from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.security import ensure_csrf_token

templates = Jinja2Templates(directory="app/templates")


def local_datetime(value: datetime | None, format_string: str = "%d %b %Y, %I:%M %p") -> str:
    if not value:
        return "Not set"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(get_settings().timezone)).strftime(format_string)


templates.env.filters["local_datetime"] = local_datetime


def flash(request: Request, message: str, category: str = "info") -> None:
    flashes = list(request.session.get("flashes", []))
    flashes.append({"message": message[:500], "category": category})
    request.session["flashes"] = flashes[-5:]


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    values = dict(context or {})
    settings = get_settings()
    values.update(
        {
            "request": request,
            "csrf_token": ensure_csrf_token(request),
            "flashes": request.session.pop("flashes", []),
            "config": {
                "app_name": settings.app_name,
                "demo_mode": settings.demo_mode,
                "emergency_number": settings.emergency_number,
                "patient_email": settings.demo_patient_email,
                "staff_email": settings.demo_staff_email,
            },
        }
    )
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=values,
        status_code=status_code,
    )
