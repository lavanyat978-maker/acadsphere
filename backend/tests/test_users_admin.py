"""Account administration, first-HOD bootstrap and authentication hardening."""
from __future__ import annotations

import pyotp
from fastapi.testclient import TestClient

from app.cli import main as cli_main
from app.core.security import open_secret
from app.db.session import session_scope
from app.models.tables import User


def _login(client: TestClient, email: str, password: str):
    return TestClient(client.app).post("/api/auth/login", json={"email": email, "password": password})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_only_hod_can_manage_users(client: TestClient, tokens):
    for role in ("teacher", "student"):
        assert client.get("/api/users", headers=tokens[role]).status_code == 403
    response = client.get("/api/users", headers=tokens["hod"])
    assert response.status_code == 200
    roles = {row["role"] for row in response.json()}
    assert {"HOD", "TEACHER", "STUDENT"} <= roles


def test_created_user_must_change_temporary_password(client: TestClient, tokens, seeded):
    response = client.post(
        "/api/users",
        headers=tokens["hod"],
        json={
            "email": "new.student@test.demo",
            "full_name": "New Student",
            "role": "STUDENT",
            "subject_ids": [seeded["subject_id"]],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    temporary = body["temporary_password"]
    assert temporary and body["user"]["must_change_password"] is True
    assert body["user"]["subject_ids"] == [seeded["subject_id"]]

    login = _login(client, "new.student@test.demo", temporary)
    assert login.status_code == 200
    headers = _bearer(login.json()["access_token"])
    assert login.json()["user"]["must_change_password"] is True

    blocked = client.get("/api/practice/tests", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "PASSWORD_CHANGE_REQUIRED"
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    reused = client.post(
        "/api/auth/password",
        headers=headers,
        json={"current_password": temporary, "new_password": temporary},
    )
    assert reused.status_code == 400

    changed = client.post(
        "/api/auth/password",
        headers=headers,
        json={"current_password": temporary, "new_password": "Brand#NewPass9"},
    )
    assert changed.status_code == 200, changed.text
    fresh = _login(client, "new.student@test.demo", "Brand#NewPass9")
    tests = client.get("/api/practice/tests", headers=_bearer(fresh.json()["access_token"]))
    assert tests.status_code == 200
    assert any(test["id"] == seeded["test_id"] for test in tests.json())


def test_duplicate_email_is_rejected(client: TestClient, tokens):
    response = client.post(
        "/api/users",
        headers=tokens["hod"],
        json={"email": "teacher@test.demo", "full_name": "Dup", "role": "TEACHER"},
    )
    assert response.status_code == 409


def test_deactivation_ends_sessions_and_hod_cannot_deactivate_self(client: TestClient, tokens):
    created = client.post(
        "/api/users",
        headers=tokens["hod"],
        json={"email": "leaver@test.demo", "full_name": "Leaving Teacher", "role": "TEACHER", "password": "Leaver#Pass12"},
    ).json()
    user_id = created["user"]["id"]
    with session_scope() as db:
        db.get(User, user_id).must_change_password = False
    login = _login(client, "leaver@test.demo", "Leaver#Pass12")
    headers = _bearer(login.json()["access_token"])
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    response = client.patch(f"/api/users/{user_id}", headers=tokens["hod"], json={"is_active": False})
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert _login(client, "leaver@test.demo", "Leaver#Pass12").status_code == 401

    me = client.get("/api/auth/me", headers=tokens["hod"]).json()
    assert client.patch(f"/api/users/{me['id']}", headers=tokens["hod"], json={"is_active": False}).status_code == 403


def test_reset_password_issues_new_temporary_password(client: TestClient, tokens, seeded):
    response = client.post(f"/api/users/{seeded['student2_id']}/reset-password", headers=tokens["hod"])
    assert response.status_code == 200
    temporary = response.json()["temporary_password"]
    login = _login(client, "student2@test.demo", temporary)
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True
    # Restore the fixture password so later tests keep working.
    client.post(
        "/api/auth/password",
        headers=_bearer(login.json()["access_token"]),
        json={"current_password": temporary, "new_password": "TestPass#123"},
    )


def test_first_hod_bootstrap_via_cli_and_mfa_enrollment(client: TestClient, capsys):
    assert cli_main(["create-user", "--role", "HOD", "--email", "boot.hod@test.demo", "--name", "Boot HOD"]) == 0
    temporary = capsys.readouterr().out.split("Temporary password (shown once): ")[1].split()[0]

    login = _login(client, "boot.hod@test.demo", temporary)
    assert login.status_code == 200, login.text
    challenge = login.json()
    assert challenge["mfa_required"] and challenge["enrollment_required"]
    assert challenge["otpauth_uri"].startswith("otpauth://")

    bad = TestClient(client.app).post(
        "/api/auth/mfa/enroll", json={"mfa_token": challenge["mfa_token"], "code": "000000"}
    )
    assert bad.status_code == 401
    code = pyotp.TOTP(challenge["secret"]).now()
    enrolled = TestClient(client.app).post(
        "/api/auth/mfa/enroll", json={"mfa_token": challenge["mfa_token"], "code": code}
    )
    assert enrolled.status_code == 200, enrolled.text
    session = enrolled.json()
    assert session["user"]["mfa_enabled"] is True
    assert session["user"]["must_change_password"] is True

    # The enrolment token cannot be replayed once MFA is active.
    replay = TestClient(client.app).post(
        "/api/auth/mfa/enroll", json={"mfa_token": challenge["mfa_token"], "code": code}
    )
    assert replay.status_code in {401, 409}

    # The next sign-in is a normal TOTP challenge.
    again = _login(client, "boot.hod@test.demo", temporary).json()
    assert again["mfa_required"] and not again["enrollment_required"]


def test_mfa_code_guessing_is_locked_out(client: TestClient):
    with session_scope() as db:
        from app.core.security import hash_password, new_totp_secret, seal_secret
        from app.models.enums import Role

        user = User(
            email="guess.hod@test.demo",
            full_name="Guess HOD",
            role=Role.HOD,
            password_hash=hash_password("TestPass#123"),
            mfa_enabled=True,
            mfa_secret_enc=seal_secret(new_totp_secret()),
        )
        db.add(user)
    challenge = _login(client, "guess.hod@test.demo", "TestPass#123").json()
    statuses = []
    for _ in range(6):
        response = TestClient(client.app).post(
            "/api/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": "000000"}
        )
        statuses.append(response.status_code)
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 423  # locked, even before checking the code

    with session_scope() as db:
        user = db.query(User).filter_by(email="guess.hod@test.demo").one()
        secret = open_secret(user.mfa_secret_enc)
    locked = TestClient(client.app).post(
        "/api/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": pyotp.TOTP(secret).now()}
    )
    assert locked.status_code == 423


def test_forwarded_for_is_ignored_without_trusted_proxy():
    from starlette.requests import Request

    from app.core import runtime
    from app.core.config import settings

    def request(header: str) -> Request:
        return Request(
            {
                "type": "http",
                "headers": [(b"x-forwarded-for", header.encode())],
                "client": ("10.0.0.5", 1234),
            }
        )

    assert runtime.client_ip(request("1.2.3.4")) == "10.0.0.5"
    original = settings.TRUSTED_PROXY_COUNT
    try:
        object.__setattr__(settings, "TRUSTED_PROXY_COUNT", 1)
        # The client forged "6.6.6.6"; the proxy appended the real address last.
        assert runtime.client_ip(request("6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    finally:
        object.__setattr__(settings, "TRUSTED_PROXY_COUNT", original)


def test_subject_admin_enrolment_and_topics(client: TestClient, tokens, seeded):
    subject_id = seeded["subject_id"]
    assert client.get(f"/api/subjects/{subject_id}/students", headers=tokens["teacher"]).status_code == 200
    assert (
        client.post(
            f"/api/subjects/{subject_id}/students", headers=tokens["teacher"], json={"student_ids": [seeded["student_id"]]}
        ).status_code
        == 403
    )
    subjects = client.get("/api/subjects", headers=tokens["teacher"]).json()
    mine = {row["id"]: row["assigned_to_me"] for row in subjects}
    assert mine[subject_id] is True

    topic = client.post(
        f"/api/subjects/{subject_id}/topics", headers=tokens["teacher"], json={"name": "Temporary Topic"}
    ).json()
    renamed = client.patch(
        f"/api/subjects/{subject_id}/topics/{topic['id']}", headers=tokens["teacher"], json={"name": "Renamed Topic"}
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed Topic"
    assert client.delete(f"/api/subjects/{subject_id}/topics/{topic['id']}", headers=tokens["teacher"]).status_code == 200
    in_use = client.delete(f"/api/subjects/{subject_id}/topics/{seeded['topic_ids'][0]}", headers=tokens["teacher"])
    assert in_use.status_code == 409


def test_totp_code_cannot_be_replayed(client: TestClient):
    from app.core.security import hash_password, new_totp_secret, seal_secret
    from app.models.enums import Role

    secret = new_totp_secret()
    with session_scope() as db:
        db.add(
            User(
                email="replay.hod@test.demo",
                full_name="Replay HOD",
                role=Role.HOD,
                password_hash=hash_password("TestPass#123"),
                mfa_enabled=True,
                mfa_secret_enc=seal_secret(secret),
            )
        )
    code = pyotp.TOTP(secret).now()
    first = _login(client, "replay.hod@test.demo", "TestPass#123").json()
    ok = TestClient(client.app).post("/api/auth/mfa/verify", json={"mfa_token": first["mfa_token"], "code": code})
    assert ok.status_code == 200
    second = _login(client, "replay.hod@test.demo", "TestPass#123").json()
    replay = TestClient(client.app).post("/api/auth/mfa/verify", json={"mfa_token": second["mfa_token"], "code": code})
    assert replay.status_code == 401
    # The demo helper hands out a code that has not been used yet.
    fresh = TestClient(client.app).post("/api/auth/mfa/challenge-code", json={"mfa_token": second["mfa_token"]}).json()
    assert fresh["code"] != code
    assert TestClient(client.app).post(
        "/api/auth/mfa/verify", json={"mfa_token": second["mfa_token"], "code": fresh["code"]}
    ).status_code == 200


def test_csv_import_is_all_or_nothing(client: TestClient, tokens):
    bad = "email,full_name,role,subjects\nok1@test.demo,Okay One,STUDENT,CS901\nnot-an-email,Broken,STUDENT,\nok2@test.demo,Okay Two,WIZARD,\n"
    response = client.post(
        "/api/users/import", headers=tokens["hod"], files={"file": ("people.csv", bad.encode(), "text/csv")}
    )
    assert response.status_code == 422
    assert {row["row"] for row in response.json()["details"]} == {3, 4}
    assert all(row["email"] != "ok1@test.demo" for row in client.get("/api/users", headers=tokens["hod"]).json())

    good = "﻿Email,Full_Name,Role,Department,Subjects\ncsv.a@test.demo,CSV Student A,,Computer Science,CS901\ncsv.t@test.demo,CSV Teacher,teacher,,cs901\n"
    response = client.post(
        "/api/users/import", headers=tokens["hod"], files={"file": ("people.csv", good.encode(), "text/csv")}
    )
    assert response.status_code == 201, response.text
    created = {row["email"]: row for row in response.json()["created"]}
    assert set(created) == {"csv.a@test.demo", "csv.t@test.demo"}
    assert created["csv.t@test.demo"]["role"] == "TEACHER" and created["csv.a@test.demo"]["temporary_password"]
    listed = {row["email"]: row for row in client.get("/api/users", headers=tokens["hod"]).json()}
    assert listed["csv.a@test.demo"]["subject_ids"] and listed["csv.t@test.demo"]["must_change_password"]
    assert client.post(
        "/api/users/import", headers=tokens["teacher"], files={"file": ("p.csv", good.encode(), "text/csv")}
    ).status_code == 403
