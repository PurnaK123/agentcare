from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

import app.models  # noqa: F401
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Role, User
from app.presentation import render
from app.seed import seed_database
from app.web import router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_for_startup()
    settings.resolve_data_paths()
    if settings.auto_create_tables:
        Base.metadata.create_all(engine)
    if settings.seed_demo_data:
        with SessionLocal() as db:
            seed_database(db)
    yield


app = FastAPI(
    title="AgentCare",
    description="Synthetic-data administrative patient coordination demo",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="agentcare_session",
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.cookie_secure,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; img-src 'self' data:; "
        "script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    if settings.demo_mode:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    accepts_html = "text/html" in request.headers.get("accept", "")
    if exc.status_code == 401 and accepts_html:
        return RedirectResponse("/login", status_code=303)
    if accepts_html:
        return render(
            request,
            "error.html",
            {"title": "Request error", "status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/", include_in_schema=False)
def index(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    with SessionLocal() as db:
        user = db.get(User, int(user_id))
        if not user or not user.active:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        destination = "/patient" if user.role == Role.PATIENT else "/staff"
    return RedirectResponse(destination, status_code=303)


@app.get("/health", name="health")
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "connected",
        "llm_configured": bool(settings.openai_api_key),
        "demo_mode": settings.demo_mode,
    }
