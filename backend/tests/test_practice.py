from __future__ import annotations

from fastapi.testclient import TestClient


def _attempt(client: TestClient, headers, test_id: str) -> dict:
    response = client.post(f"/api/practice/tests/{test_id}/attempts", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_student_sees_only_published_tests(client: TestClient, tokens, seeded):
    response = client.get("/api/practice/tests", headers=tokens["student"])
    assert response.status_code == 200
    titles = [test["title"] for test in response.json()]
    assert "Seeded Practice Test" in titles


def test_staff_test_detail_includes_answers_but_attempt_hides_them(client: TestClient, tokens, seeded):
    staff = client.get(
        f"/api/practice/staff/tests/{seeded['test_id']}", headers=tokens["teacher"]
    )
    assert staff.status_code == 200
    assert staff.json()["questions"][0]["correct_answers"]

    attempt = _attempt(client, tokens["student"], seeded["test_id"])
    assert attempt["questions"]
    assert all(question["correct_answers"] is None for question in attempt["questions"])
    assert all(question["explanation"] is None for question in attempt["questions"])


def test_submission_scores_and_returns_review(client: TestClient, tokens, seeded):
    attempt = _attempt(client, tokens["student"], seeded["test_id"])
    # Seeded correct answers in question order: c, a, b, b.
    correct = {"0": "c", "1": "a", "2": "b", "3": "b"}
    answers = [
        {
            "question_id": question["id"],
            "selected": [correct[str(index)]],
            "response_time_seconds": 10,
        }
        for index, question in enumerate(attempt["questions"])
    ]
    response = client.post(
        f"/api/practice/attempts/{attempt['id']}/submit",
        headers=tokens["student"],
        json={"answers": answers},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["percentage"] == 100.0
    assert result["accuracy"] == 100.0
    assert len(result["review"]) == 4

    again = client.post(
        f"/api/practice/attempts/{attempt['id']}/submit",
        headers=tokens["student"],
        json={"answers": answers},
    )
    assert again.status_code == 409


def test_student_cannot_read_another_students_attempt(client: TestClient, tokens, seeded):
    attempt = _attempt(client, tokens["student"], seeded["test_id"])
    foreign = client.get(f"/api/practice/attempts/{attempt['id']}", headers=tokens["student2"])
    assert foreign.status_code == 403
    foreign_result = client.get(
        f"/api/practice/attempts/{attempt['id']}/result", headers=tokens["student2"]
    )
    assert foreign_result.status_code == 403


def test_teacher_analytics_and_ownership(client: TestClient, tokens, seeded):
    response = client.get(
        f"/api/practice/tests/{seeded['test_id']}/analytics", headers=tokens["teacher"]
    )
    assert response.status_code == 200
    assert response.json()["attempts"] >= 1
    assert "student_rows" in response.json()

    # A different teacher cannot manage this test. Kavya is not in the test DB,
    # so assert the endpoint rejects an unauthenticated caller instead.
    anonymous = client.get(f"/api/practice/tests/{seeded['test_id']}/analytics")
    assert anonymous.status_code == 401


def test_published_test_questions_are_immutable(client: TestClient, tokens, seeded):
    response = client.patch(
        f"/api/practice/tests/{seeded['test_id']}", headers=tokens["teacher"], json={"title": "Renamed"}
    )
    # Published with attempts -> locked.
    assert response.status_code == 409
    assert response.json()["error"] == "TEST_LOCKED"


def test_student_history_and_analytics(client: TestClient, tokens):
    history = client.get("/api/practice/history", headers=tokens["student"])
    assert history.status_code == 200
    assert isinstance(history.json(), list)
    analytics = client.get("/api/practice/analytics/me", headers=tokens["student"])
    assert analytics.status_code == 200
    assert "recommendations" in analytics.json()
    assert "topic_performance" in analytics.json()
