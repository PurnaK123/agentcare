import re

import pytest
from fastapi.testclient import TestClient

from app import web as web_module
from app.config import get_settings
from app.database import get_db
from app.main import app
from tests.fakes import FakeLLM


def csrf_from(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


@pytest.fixture
def client(db, monkeypatch):
    def override_db():
        yield db

    monkeypatch.setattr(web_module, "OpenAIJsonClient", lambda: FakeLLM())
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def login(client: TestClient, email: str, password: str):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf_from(page)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response


def test_patient_interface_uses_real_workflow_and_blocks_staff_route(client):
    settings = get_settings()
    response = login(client, settings.demo_patient_email, settings.demo_patient_password)
    assert response.headers["location"] == "/patient"

    dashboard = client.get("/patient")
    assert dashboard.status_code == 200
    assert "Synthetic profile" not in dashboard.text
    forbidden = client.get("/staff", headers={"accept": "text/html"})
    assert forbidden.status_code == 403

    request_page = client.get("/patient/requests/new")
    submitted = client.post(
        "/patient/requests",
        data={
            "request_text": "I need a Cardiology appointment next week.",
            "synthetic_acknowledgement": "yes",
            "csrf_token": csrf_from(request_page),
        },
        files={
            "documents": (
                "synthetic-ecg.txt",
                b"Synthetic ECG report for testing only.",
                "text/plain",
            )
        },
        follow_redirects=False,
    )
    assert submitted.status_code == 303
    detail = client.get(submitted.headers["location"])
    assert detail.status_code == 200
    assert "Coordinator Agent" in detail.text
    assert "book_appointment" in detail.text
    assert "blood_report" in detail.text


def test_staff_interface_is_inaccessible_without_staff_session(client):
    response = client.get("/staff", headers={"accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    settings = get_settings()
    login(client, settings.demo_staff_email, settings.demo_staff_password)
    staff_page = client.get("/staff")
    assert staff_page.status_code == 200
    assert "Human oversight desk" in staff_page.text
    assert client.get("/staff/catalog").status_code == 200
    assert client.get("/staff/audit").status_code == 200
    patient_page = client.get("/patient", headers={"accept": "text/html"})
    assert patient_page.status_code == 403


def test_mutating_form_rejects_invalid_csrf(client):
    settings = get_settings()
    login(client, settings.demo_patient_email, settings.demo_patient_password)
    response = client.post(
        "/patient/profile",
        data={
            "name": "Synthetic Name",
            "csrf_token": "forged-token",
            "demo_acknowledgement": "yes",
        },
        headers={"accept": "text/html"},
    )
    assert response.status_code == 403
