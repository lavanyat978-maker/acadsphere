from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.session import session_scope
from app.models.tables import AuditLog


def test_health_is_public(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_wrong_password_is_rejected(client: TestClient):
    response = client.post(
        "/api/auth/login", json={"email": "student@test.demo", "password": "not-the-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"


def test_hod_requires_mfa(client: TestClient, tokens):
    response = client.post(
        "/api/auth/login", json={"email": "hod@test.demo", "password": "TestPass#123"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mfa_required"] is True
    assert "access_token" not in body

    failure = client.post(
        "/api/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": "000000"}
    )
    assert failure.status_code == 401


def test_student_cannot_reach_staff_endpoints(client: TestClient, tokens):
    assert client.get("/api/exams", headers=tokens["student"]).status_code == 403
    assert client.get("/api/audit/logs", headers=tokens["student"]).status_code == 403
    assert (
        client.get("/api/exams/release/queue", headers=tokens["student"]).status_code == 403
    )
    assert (
        client.get("/api/practice/staff/tests", headers=tokens["student"]).status_code == 403
    )


def test_teacher_cannot_use_hod_release(client: TestClient, tokens):
    assert client.get("/api/audit/logs", headers=tokens["teacher"]).status_code == 403
    assert client.get("/api/exams/release/queue", headers=tokens["teacher"]).status_code == 403


def test_denied_access_is_audited(client: TestClient, tokens):
    before = 0
    with session_scope() as db:
        before = len(
            db.query(AuditLog).filter(AuditLog.event == "UNAUTHORIZED_ACCESS_ATTEMPT").all()
        )
    response = client.get("/api/audit/logs", headers=tokens["student"])
    assert response.status_code == 403
    with session_scope() as db:
        after = len(
            db.query(AuditLog).filter(AuditLog.event == "UNAUTHORIZED_ACCESS_ATTEMPT").all()
        )
    assert after >= before + 1


def test_csrf_required_for_cookie_sessions(client: TestClient):
    fresh = TestClient(client.app)
    login = fresh.post(
        "/api/auth/login", json={"email": "student@test.demo", "password": "TestPass#123"}
    )
    assert login.status_code == 200
    assert fresh.cookies.get("access_token")
    csrf = fresh.cookies.get("csrf_token")
    assert csrf

    # Cookie-authenticated unsafe request without the CSRF header is rejected.
    blocked = fresh.post("/api/practice/tests/anything/attempts")
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "CSRF_FAILED"

    # The same request with the double-submit header passes CSRF and then
    # fails authorization/business rules instead of the CSRF check.
    allowed_past_csrf = fresh.post(
        "/api/practice/tests/anything/attempts", headers={"X-CSRF-Token": csrf}
    )
    assert allowed_past_csrf.status_code != 403 or (
        allowed_past_csrf.json().get("error") != "CSRF_FAILED"
    )


def test_password_change_revokes_old_sessions(client: TestClient):
    from app.core.security import hash_password
    from app.models.tables import User

    with session_scope() as db:
        db.add(
            User(
                email="password@test.demo",
                full_name="Password Tester",
                role="STUDENT",
                department="Computer Science",
                password_hash=hash_password("TestPass#123"),
            )
        )
        db.commit()
    fresh = TestClient(client.app)
    login = fresh.post(
        "/api/auth/login", json={"email": "password@test.demo", "password": "TestPass#123"}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    changed = fresh.post(
        "/api/auth/password",
        headers=headers,
        json={
            "current_password": "TestPass#123",
            "new_password": "NewTestPass#123",
        },
    )
    assert changed.status_code == 200

    stale = fresh.get("/api/auth/me", headers=headers)
    assert stale.status_code == 401
    assert stale.json()["error"] == "SESSION_REVOKED"

    # The refreshed cookie session keeps working after the rotation.
    current = fresh.get("/api/auth/me")
    assert current.status_code == 200
    assert current.json()["email"] == "password@test.demo"
