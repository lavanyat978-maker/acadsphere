"""Uploaded papers, HOD-as-superset, co-teacher view rights, failure recovery,
cancellation and the per-paper print cap."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.provider import DemoProvider, set_provider
from app.core.security import hash_password
from app.db.session import session_scope
from app.models.enums import ExamStatus, PaperState, Role
from app.models.tables import InternalExam, QuestionPaper, QuestionPool, User
from app.services import academic_service
from app.services.generation_service import run_generation
from app.storage.backend import get_storage

from .conftest import PASSWORD, _token_headers, exam_payload


def _pdf(text: str = "Section A - answer all questions", pages: int = 2) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    for index in range(pages):
        page.drawString(72, 720, f"{text} (page {index + 1})")
        page.showPage()
    page.save()
    return buffer.getvalue()


def _uploaded_exam(client: TestClient, headers: dict, subject_id: str, *, days: int = 3) -> dict:
    payload = {
        "source": "UPLOADED",
        "subject_id": subject_id,
        "title": "Prepared Paper Assessment",
        "exam_type": "Internal Assessment 2",
        "exam_date": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
        "duration_minutes": 60,
        "venue": "Hall B",
        "total_marks": 30,
    }
    response = client.post("/api/exams", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client: TestClient, headers: dict, exam_id: str, data: bytes, name: str = "paper.pdf"):
    return client.post(
        f"/api/exams/{exam_id}/paper",
        headers=headers,
        files={"file": (name, data, "application/pdf")},
    )


def _move_exam_into_window(exam_id: str) -> None:
    with session_scope() as db:
        db.get(InternalExam, exam_id).exam_date = datetime.now(timezone.utc) + timedelta(minutes=45)


@pytest.fixture(scope="module")
def co_teacher(client, seeded):
    with session_scope() as db:
        user = User(
            email="coteacher@test.demo",
            full_name="Co Teacher",
            role=Role.TEACHER,
            password_hash=hash_password(PASSWORD),
        )
        db.add(user)
        db.flush()
        academic_service.assign_teacher(db, seeded["subject_id"], user.id)
    return _token_headers(client, "coteacher@test.demo")


def test_uploaded_paper_is_sealed_and_replaceable_until_window(client: TestClient, tokens, seeded):
    exam = _uploaded_exam(client, tokens["teacher"], seeded["subject_id"])
    assert exam["source"] == "UPLOADED" and exam["status"] == "DRAFT" and exam["can_edit"] is True

    generate = client.post(f"/api/exams/{exam['id']}/generate", headers=tokens["teacher"])
    assert generate.status_code == 409

    bad = _upload(client, tokens["teacher"], exam["id"], b"not a pdf", "paper.pdf")
    assert bad.status_code == 400 and bad.json()["error"] == "PAPER_NOT_PDF"

    secret_text = "UNIQUE-SEALED-QUESTION-TEXT"
    first = _upload(client, tokens["teacher"], exam["id"], _pdf(secret_text, pages=3))
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["status"] == "SECURED" and body["paper_state"] == "STORED"
    assert body["paper_version"] == 1 and body["paper_page_count"] == 3

    # Stored only as ciphertext.
    with session_scope() as db:
        paper = db.get(QuestionPaper, body["paper_id"])
        from app.models.tables import EncryptedPaper

        bundle = db.execute(select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)).scalar_one()
        stored = get_storage().get(bundle.ciphertext_path)
        assert not stored.startswith(b"%PDF") and secret_text.encode() not in stored
        first_path = bundle.ciphertext_path

    metadata = client.get(f"/api/exams/{exam['id']}/paper", headers=tokens["teacher"]).json()
    assert metadata["content_type"] == "application/pdf" and metadata["page_count"] == 3
    assert secret_text not in str(metadata)

    second = _upload(client, tokens["teacher"], exam["id"], _pdf("Revised paper", pages=2))
    assert second.status_code == 201
    assert second.json()["paper_version"] == 2
    with session_scope() as db:
        old = db.execute(
            select(QuestionPaper).where(QuestionPaper.exam_id == exam["id"], QuestionPaper.version == 1)
        ).scalar_one()
        assert old.state == PaperState.DISCARDED
    assert not get_storage().exists(first_path)

    _move_exam_into_window(exam["id"])
    frozen = _upload(client, tokens["teacher"], exam["id"], _pdf("Too late"))
    assert frozen.status_code == 409 and frozen.json()["error"] == "PAPER_FROZEN"


def test_uploaded_paper_release_prints_stamped_original_and_cap_survives_reauth(
    client: TestClient, tokens, seeded
):
    exam = _uploaded_exam(client, tokens["teacher"], seeded["subject_id"])
    paper_id = _upload(client, tokens["teacher"], exam["id"], _pdf(pages=2)).json()["paper_id"]
    _move_exam_into_window(exam["id"])

    queue = client.get("/api/exams/release/queue", headers=tokens["hod"]).json()
    item = next(row for row in queue if row["paper_id"] == paper_id)
    assert item["source"] == "UPLOADED" and item["prints_remaining"] == 3

    def authorize() -> str:
        response = client.post(
            f"/api/exams/release/{paper_id}/authorize",
            headers=tokens["hod"],
            json={"reason": "Scheduled release for uploaded paper"},
        )
        assert response.status_code == 200, response.text
        return response.json()["release_token"]

    token = authorize()
    decrypt = client.post(
        f"/api/exams/release/{paper_id}/decrypt",
        headers=tokens["hod"],
        json={"release_token": token, "reason": "Integrity check before printing"},
    )
    assert decrypt.status_code == 200 and decrypt.json()["page_count"] == 2

    def print_copy(release_token: str):
        return client.get(
            f"/api/exams/release/{paper_id}/pdf", headers=tokens["hod"], params={"release_token": release_token}
        )

    copy = print_copy(token)
    assert copy.status_code == 200 and copy.content.startswith(b"%PDF")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(copy.content))
    assert len(reader.pages) == 2
    assert "Controlled copy 1/3" in reader.pages[0].extract_text()
    assert print_copy(token).status_code == 200

    # Re-authorising must not reset the per-paper cap.
    token = authorize()
    assert print_copy(token).headers["x-prints-remaining"] == "0"
    blocked = print_copy(token)
    assert blocked.status_code == 409 and blocked.json()["error"] == "PRINT_LIMIT_REACHED"


def test_co_teacher_can_view_but_not_change(client: TestClient, tokens, seeded, co_teacher):
    exam = _uploaded_exam(client, tokens["teacher"], seeded["subject_id"])
    listed = client.get("/api/exams", headers=co_teacher).json()
    row = next(item for item in listed if item["id"] == exam["id"])
    assert row["can_edit"] is False
    assert client.get(f"/api/exams/{exam['id']}", headers=co_teacher).status_code == 200
    assert _upload(client, co_teacher, exam["id"], _pdf()).status_code == 403
    cancel = client.post(f"/api/exams/{exam['id']}/cancel", headers=co_teacher, json={"reason": "Not mine to cancel"})
    assert cancel.status_code == 403

    test_view = client.get(f"/api/practice/staff/tests/{seeded['test_id']}", headers=co_teacher)
    assert test_view.status_code == 200 and test_view.json()["can_edit"] is False
    assert (
        client.post(
            f"/api/practice/tests/{seeded['test_id']}/status", headers=co_teacher, json={"status": "ARCHIVED"}
        ).status_code
        == 403
    )


def test_unassigned_teacher_cannot_see_subject_exams(client: TestClient, tokens, seeded):
    with session_scope() as db:
        db.add(
            User(
                email="outsider@test.demo",
                full_name="Outsider",
                role=Role.TEACHER,
                password_hash=hash_password(PASSWORD),
            )
        )
    outsider = _token_headers(client, "outsider@test.demo")
    exam = _uploaded_exam(client, tokens["teacher"], seeded["subject_id"])
    assert client.get(f"/api/exams/{exam['id']}", headers=outsider).status_code == 403
    assert all(item["id"] != exam["id"] for item in client.get("/api/exams", headers=outsider).json())
    assert client.get("/api/exams", headers=tokens["student"]).status_code == 403


def test_hod_can_create_and_generate_any_exam(client: TestClient, tokens, seeded):
    response = client.post(
        "/api/exams", headers=tokens["hod"], json=exam_payload(seeded["subject_id"], seeded["topic_ids"])
    )
    assert response.status_code == 201, response.text
    exam_id = response.json()["id"]
    assert client.post(f"/api/exams/{exam_id}/submit", headers=tokens["hod"]).status_code == 200
    queued = client.post(f"/api/exams/{exam_id}/generate", headers=tokens["hod"])
    assert queued.status_code == 200
    run_generation(queued.json()["request_id"])
    detail = client.get(f"/api/exams/{exam_id}", headers=tokens["hod"]).json()
    assert detail["status"] == "SECURED" and detail["paper_state"] == "STORED"

    # Teachers in the subject can see the HOD's exam but not change it.
    teacher_view = client.get(f"/api/exams/{exam_id}", headers=tokens["teacher"]).json()
    assert teacher_view["can_edit"] is False

    # Every candidate's plaintext is gone, not only the selected ones.
    with session_scope() as db:
        rows = db.execute(select(QuestionPool).where(QuestionPool.exam_id == exam_id)).scalars().all()
        assert rows and all(row.question_text is None for row in rows)


class _ExplodingProvider(DemoProvider):
    def generate_questions(self, *, spec, context):  # noqa: D401
        raise RuntimeError("provider outage")


def test_failed_generation_can_be_retried(client: TestClient, tokens, seeded):
    response = client.post(
        "/api/exams", headers=tokens["teacher"], json=exam_payload(seeded["subject_id"], seeded["topic_ids"])
    )
    exam_id = response.json()["id"]
    client.post(f"/api/exams/{exam_id}/submit", headers=tokens["teacher"])

    set_provider(_ExplodingProvider())
    try:
        request_id = client.post(f"/api/exams/{exam_id}/generate", headers=tokens["teacher"]).json()["request_id"]
        run_generation(request_id)
    finally:
        set_provider(None)

    failed = client.get(f"/api/exams/{exam_id}", headers=tokens["teacher"]).json()
    assert failed["status"] == "FAILED"
    assert failed["generation_status"] == "FAILED"
    assert failed["paper_state"] == "DISCARDED"

    retry = client.post(f"/api/exams/{exam_id}/generate", headers=tokens["teacher"])
    assert retry.status_code == 200, retry.text
    run_generation(retry.json()["request_id"])
    secured = client.get(f"/api/exams/{exam_id}", headers=tokens["teacher"]).json()
    assert secured["status"] == "SECURED" and secured["paper_version"] == 2


def test_crash_outside_candidate_loop_is_recorded(client: TestClient, tokens, seeded, monkeypatch):
    response = client.post(
        "/api/exams", headers=tokens["teacher"], json=exam_payload(seeded["subject_id"], seeded["topic_ids"])
    )
    exam_id = response.json()["id"]
    client.post(f"/api/exams/{exam_id}/submit", headers=tokens["teacher"])
    request_id = client.post(f"/api/exams/{exam_id}/generate", headers=tokens["teacher"]).json()["request_id"]

    import app.services.generation_service as generation

    def boom(*args, **kwargs):
        raise RuntimeError("retrieval backend down")

    monkeypatch.setattr(generation, "retrieve", boom)
    run_generation(request_id)
    status = client.get(f"/api/exams/{exam_id}/generation", headers=tokens["teacher"]).json()
    assert status["status"] == "FAILED" and "RuntimeError" in status["error"]
    monkeypatch.undo()
    assert client.post(f"/api/exams/{exam_id}/generate", headers=tokens["teacher"]).status_code == 200


def test_cancel_exam_discards_paper(client: TestClient, tokens, seeded):
    exam = _uploaded_exam(client, tokens["teacher"], seeded["subject_id"])
    paper_id = _upload(client, tokens["teacher"], exam["id"], _pdf()).json()["paper_id"]
    cancel = client.post(
        f"/api/exams/{exam['id']}/cancel", headers=tokens["teacher"], json={"reason": "Exam moved to next term"}
    )
    assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"
    assert cancel.json()["paper_state"] == "DISCARDED"
    queue = client.get("/api/exams/release/queue", headers=tokens["hod"]).json()
    assert all(row["paper_id"] != paper_id for row in queue)
    again = client.post(
        f"/api/exams/{exam['id']}/cancel", headers=tokens["teacher"], json={"reason": "Second cancellation"}
    )
    assert again.status_code == 409


def test_draft_exam_can_be_edited_by_owner_only(client: TestClient, tokens, seeded, co_teacher):
    payload = exam_payload(seeded["subject_id"], seeded["topic_ids"])
    exam_id = client.post("/api/exams", headers=tokens["teacher"], json=payload).json()["id"]
    payload["title"] = "Renamed Assessment"
    assert client.put(f"/api/exams/{exam_id}", headers=co_teacher, json=payload).status_code == 403
    updated = client.put(f"/api/exams/{exam_id}", headers=tokens["teacher"], json=payload)
    assert updated.status_code == 200 and updated.json()["title"] == "Renamed Assessment"
    payload["source"] = "UPLOADED"
    assert client.put(f"/api/exams/{exam_id}", headers=tokens["teacher"], json=payload).status_code == 409


def test_integrity_check_is_audited(client: TestClient, tokens, secured_paper):
    paper_id = client.get(f"/api/exams/{secured_paper['exam_id']}/paper", headers=tokens["hod"]).json()["paper_id"]
    assert client.get(f"/api/exams/papers/{paper_id}/integrity", headers=tokens["hod"]).status_code == 200
    logs = client.get(
        "/api/audit/logs", headers=tokens["hod"], params={"event": "PAPER_INTEGRITY_VERIFIED"}
    ).json()
    assert logs["total"] >= 1


def test_exam_status_enum_includes_cancelled():
    assert ExamStatus.CANCELLED == "CANCELLED"


def test_generation_requires_syllabus_material(client: TestClient, tokens):
    from app.models.tables import Subject, Topic

    with session_scope() as db:
        subject = Subject(code="EMPTY1", name="No Material Subject", description="")
        db.add(subject)
        db.flush()
        topics = [Topic(subject_id=subject.id, name=f"Topic {i}", unit="Unit 1") for i in range(4)]
        db.add_all(topics)
        db.flush()
        subject_id, topic_ids = subject.id, [topic.id for topic in topics]
    payload = exam_payload(subject_id, topic_ids)
    response = client.post("/api/exams", headers=tokens["hod"], json=payload)
    assert response.status_code == 201, response.text
    exam_id = response.json()["id"]
    client.post(f"/api/exams/{exam_id}/submit", headers=tokens["hod"])
    blocked = client.post(f"/api/exams/{exam_id}/generate", headers=tokens["hod"])
    assert blocked.status_code == 409 and blocked.json()["error"] == "NO_SYLLABUS"
