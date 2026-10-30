"""Practice scoping, autosave, server-enforced deadline and lifecycle rules."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db.session import session_scope
from app.models.enums import Role
from app.models.tables import PracticeAttempt, PracticeQuestion, User

from .conftest import PASSWORD, _token_headers


def _questions(test_id: str) -> list[PracticeQuestion]:
    with session_scope() as db:
        rows = db.query(PracticeQuestion).filter_by(test_id=test_id).order_by(PracticeQuestion.position).all()
        return [(row.id, list(row.correct_answers)) for row in rows]


def _new_student(client: TestClient, email: str) -> dict[str, str]:
    with session_scope() as db:
        db.add(User(email=email, full_name=email.split("@")[0], role=Role.STUDENT, password_hash=hash_password(PASSWORD)))
    return _token_headers(client, email)


def _draft_test(client: TestClient, tokens, seeded, title: str) -> str:
    response = client.post(
        "/api/practice/tests",
        headers=tokens["teacher"],
        json={
            "subject_id": seeded["subject_id"],
            "title": title,
            "duration_minutes": 10,
            "topic_ids": seeded["topic_ids"][:2],
            "questions": [
                {
                    "topic_id": seeded["topic_ids"][0],
                    "question_text": f"{title}: which option is correct?",
                    "options": [{"id": "a", "text": "Alpha"}, {"id": "b", "text": "Beta"}],
                    "correct_answers": ["a"],
                },
                {
                    "topic_id": seeded["topic_ids"][1],
                    "question_text": f"{title}: pick the second option?",
                    "options": [{"id": "a", "text": "Alpha"}, {"id": "b", "text": "Beta"}],
                    "correct_answers": ["b"],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_unenrolled_student_sees_nothing(client: TestClient, seeded):
    outsider = _new_student(client, "unenrolled@test.demo")
    assert client.get("/api/practice/tests", headers=outsider).json() == []
    assert client.get(f"/api/practice/tests/{seeded['test_id']}", headers=outsider).status_code == 403
    assert client.post(f"/api/practice/tests/{seeded['test_id']}/attempts", headers=outsider).status_code == 403
    assert client.get("/api/subjects", headers=outsider).json() == []


def test_autosave_restores_and_late_submit_grades_saved_answers(client: TestClient, tokens, seeded):
    student = _new_student(client, "autosave@test.demo")
    client.post(
        f"/api/subjects/{seeded['subject_id']}/students",
        headers=tokens["hod"],
        json={"student_ids": [client.get("/api/auth/me", headers=student).json()["id"]]},
    )
    attempt = client.post(f"/api/practice/tests/{seeded['test_id']}/attempts", headers=student).json()
    questions = _questions(seeded["test_id"])
    first_id, first_correct = questions[0]
    second_id, second_correct = questions[1]

    saved = client.put(
        f"/api/practice/attempts/{attempt['id']}/answers",
        headers=student,
        json={"answers": [{"question_id": first_id, "selected": first_correct + ["zz"]}]},
    )
    assert saved.status_code == 200 and saved.json()["saved"] == 1

    resumed = client.post(f"/api/practice/tests/{seeded['test_id']}/attempts", headers=student).json()
    assert resumed["id"] == attempt["id"]
    assert resumed["saved_answers"][first_id]["selected"] == first_correct  # unknown option dropped

    # Push the deadline into the past: a late submission must not count, only the autosave.
    with session_scope() as db:
        row = db.get(PracticeAttempt, attempt["id"])
        row.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    late_save = client.put(
        f"/api/practice/attempts/{attempt['id']}/answers",
        headers=student,
        json={"answers": [{"question_id": second_id, "selected": second_correct}]},
    )
    assert late_save.status_code == 409
    result = client.get(f"/api/practice/attempts/{attempt['id']}/result", headers=student)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["auto_submitted"] is True
    assert body["score"] == 1.0
    assert client.post(
        f"/api/practice/attempts/{attempt['id']}/submit", headers=student, json={"answers": []}
    ).status_code == 409


def test_late_submission_payload_is_ignored(client: TestClient, tokens, seeded):
    student = _new_student(client, "late@test.demo")
    client.post(
        f"/api/subjects/{seeded['subject_id']}/students",
        headers=tokens["hod"],
        json={"student_ids": [client.get("/api/auth/me", headers=student).json()["id"]]},
    )
    attempt = client.post(f"/api/practice/tests/{seeded['test_id']}/attempts", headers=student).json()
    with session_scope() as db:
        row = db.get(PracticeAttempt, attempt["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    all_correct = [{"question_id": qid, "selected": correct} for qid, correct in _questions(seeded["test_id"])]
    result = client.post(
        f"/api/practice/attempts/{attempt['id']}/submit", headers=student, json={"answers": all_correct}
    )
    assert result.status_code == 200
    assert result.json()["score"] == 0 and result.json()["auto_submitted"] is True


def test_status_rules_protect_attempted_tests(client: TestClient, tokens, seeded):
    test_id = _draft_test(client, tokens, seeded, "Lifecycle Test")
    publish = client.post(f"/api/practice/tests/{test_id}/status", headers=tokens["teacher"], json={"status": "PUBLISHED"})
    assert publish.status_code == 200
    # Nobody attempted it yet: it can go back to draft.
    assert (
        client.post(f"/api/practice/tests/{test_id}/status", headers=tokens["teacher"], json={"status": "DRAFT"}).status_code
        == 200
    )
    client.post(f"/api/practice/tests/{test_id}/status", headers=tokens["teacher"], json={"status": "PUBLISHED"})
    attempt = client.post(f"/api/practice/tests/{test_id}/attempts", headers=tokens["student"]).json()
    client.post(f"/api/practice/attempts/{attempt['id']}/submit", headers=tokens["student"], json={"answers": []})
    back = client.post(f"/api/practice/tests/{test_id}/status", headers=tokens["teacher"], json={"status": "DRAFT"})
    assert back.status_code == 409 and back.json()["error"] == "TEST_HAS_ATTEMPTS"
    archived = client.post(f"/api/practice/tests/{test_id}/status", headers=tokens["teacher"], json={"status": "ARCHIVED"})
    assert archived.status_code == 200


def test_draft_questions_can_be_edited_and_deleted(client: TestClient, tokens, seeded):
    test_id = _draft_test(client, tokens, seeded, "Editable Test")
    detail = client.get(f"/api/practice/staff/tests/{test_id}", headers=tokens["teacher"]).json()
    first = detail["questions"][0]
    edited = client.put(
        f"/api/practice/tests/{test_id}/questions/{first['id']}",
        headers=tokens["teacher"],
        json={
            "topic_id": first["topic_id"],
            "question_text": "Edited question text?",
            "options": [{"id": "a", "text": "Alpha"}, {"id": "b", "text": "Beta"}, {"id": "c", "text": "Gamma"}],
            "correct_answers": ["c"],
            "marks": 2,
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["total_marks"] == 3
    deleted = client.delete(f"/api/practice/tests/{test_id}/questions/{first['id']}", headers=tokens["teacher"])
    assert deleted.status_code == 200
    assert deleted.json()["question_count"] == 1 and deleted.json()["questions"][0]["position"] == 0


def test_hod_manages_any_test_and_sees_department_rollup(client: TestClient, tokens, seeded):
    test_id = _draft_test(client, tokens, seeded, "HOD Managed Test")
    detail = client.get(f"/api/practice/staff/tests/{test_id}", headers=tokens["hod"]).json()
    assert detail["can_edit"] is True
    assert (
        client.post(f"/api/practice/tests/{test_id}/status", headers=tokens["hod"], json={"status": "ARCHIVED"}).status_code
        == 200
    )
    dashboard = client.get("/api/analytics/dashboard", headers=tokens["hod"]).json()
    subjects = {row["subject_id"]: row for row in dashboard["department"]["subjects"]}
    assert subjects[seeded["subject_id"]]["students"] >= 2


def test_teacher_analytics_aggregates_attempts_per_student(client: TestClient, tokens, seeded):
    analytics = client.get(f"/api/practice/tests/{seeded['test_id']}/analytics", headers=tokens["teacher"]).json()
    for row in analytics["student_rows"]:
        assert row["attempts"] >= 1
        assert 0 <= row["best_percentage"] <= 100


def test_hod_student_analytics_and_staff_result_review(client: TestClient, tokens, seeded):
    hod_view = client.get(f"/api/analytics/student/{seeded['student_id']}", headers=tokens["hod"])
    assert hod_view.status_code == 200 and hod_view.json()["student"]["email"] == "student@test.demo"
    assert client.get(f"/api/analytics/student/{seeded['teacher_id']}", headers=tokens["hod"]).status_code == 404
    assert client.get(f"/api/analytics/student/{seeded['student_id']}", headers=tokens["teacher"]).status_code == 403
    assert client.get(f"/api/analytics/student/{seeded['student_id']}", headers=tokens["student2"]).status_code == 403

    analytics = client.get(f"/api/practice/tests/{seeded['test_id']}/analytics", headers=tokens["teacher"]).json()
    row = analytics["student_rows"][0]
    review = client.get(f"/api/practice/staff/attempts/{row['last_attempt_id']}/result", headers=tokens["teacher"])
    assert review.status_code == 200 and review.json()["student_name"] == row["student_name"]
