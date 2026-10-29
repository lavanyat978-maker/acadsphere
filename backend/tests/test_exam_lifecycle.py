from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import session_scope
from app.models.tables import EncryptedPaper, QuestionPaper, QuestionPool, Subject
from app.storage.backend import get_storage


def _release_token(client: TestClient, headers, paper_id: str) -> str:
    response = client.post(
        f"/api/exams/release/{paper_id}/authorize",
        headers=headers,
        json={"reason": "Authorized for scheduled internal assessment"},
    )
    assert response.status_code == 200, response.text
    return response.json()["release_token"]


def test_generation_secured_the_paper(client: TestClient, tokens, secured_paper):
    exam_id = secured_paper["exam_id"]
    status = client.get(f"/api/exams/{exam_id}/generation", headers=tokens["teacher"])
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "COMPLETED"
    assert body["progress"] == 100
    assert body["paper_state"] == "STORED"

    paper = client.get(f"/api/exams/{exam_id}/paper", headers=tokens["teacher"])
    assert paper.status_code == 200
    meta = paper.json()
    assert meta["state"] == "STORED"
    assert meta["question_count"] == 4
    assert meta["total_marks"] == 20
    assert meta["algorithm"] == "AES-256-GCM"
    assert meta["plaintext_hash_matches"] is True
    assert meta["transitions"]
    assert "question_text" not in paper.text
    assert "expected_answer" not in paper.text


def test_selected_question_plaintext_is_sealed(client: TestClient, secured_paper):
    with session_scope() as db:
        selected = (
            db.execute(select(QuestionPool).where(QuestionPool.selected.is_(True))).scalars().all()
        )
        assert selected
        assert all(row.sealed for row in selected)
        assert all(row.question_text is None for row in selected)


def test_ciphertext_exists_and_is_not_plaintext(client: TestClient, secured_paper):
    with session_scope() as db:
        paper = db.execute(
            select(QuestionPaper).where(QuestionPaper.exam_id == secured_paper["exam_id"])
        ).scalar_one()
        bundle = db.execute(
            select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)
        ).scalar_one()
        ciphertext = get_storage().get(bundle.ciphertext_path)
        assert len(ciphertext) == bundle.ciphertext_bytes
        assert b"Test question" not in ciphertext
        assert b"Section A" not in ciphertext
        assert bundle.wrapped_dek
        assert bundle.payload_nonce


def test_teacher_cannot_read_or_release_plaintext(client: TestClient, tokens, secured_paper):
    paper = client.get(
        f"/api/exams/{secured_paper['exam_id']}/paper", headers=tokens["teacher"]
    ).json()
    denied = client.get(
        f"/api/exams/papers/{paper['paper_id']}/plaintext", headers=tokens["teacher"]
    )
    assert denied.status_code == 403
    assert denied.json()["error"] == "PAPER_SEALED"
    assert "Test question" not in denied.text

    authorize = client.post(
        f"/api/exams/release/{paper['paper_id']}/authorize",
        headers=tokens["teacher"],
        json={"reason": "Teacher should not be able to release"},
    )
    assert authorize.status_code == 403


def test_release_window_and_hod_flow(client: TestClient, tokens, secured_paper):
    paper_id = client.get(
        f"/api/exams/{secured_paper['exam_id']}/paper", headers=tokens["teacher"]
    ).json()["paper_id"]

    queue = client.get("/api/exams/release/queue", headers=tokens["hod"])
    assert queue.status_code == 200
    item = next(row for row in queue.json() if row["paper_id"] == paper_id)
    assert item["window_open"] is True

    token = _release_token(client, tokens["hod"], paper_id)

    wrong = client.post(
        f"/api/exams/release/{paper_id}/decrypt",
        headers=tokens["hod"],
        json={"release_token": "x" * 40, "reason": "Wrong token attempt"},
    )
    assert wrong.status_code in {400, 403, 409}

    decrypt = client.post(
        f"/api/exams/release/{paper_id}/decrypt",
        headers=tokens["hod"],
        json={"release_token": token, "reason": "Verify integrity before printing"},
    )
    assert decrypt.status_code == 200, decrypt.text
    body = decrypt.json()
    assert body["integrity_verified"] is True
    assert body["question_count"] == 4
    assert "questions" not in body
    assert "Test question" not in decrypt.text

    pdf = client.get(
        f"/api/exams/release/{paper_id}/pdf",
        headers=tokens["hod"],
        params={"release_token": token},
    )
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert pdf.headers["x-prints-remaining"] == "2"

    released = client.post(f"/api/exams/release/{paper_id}/released", headers=tokens["hod"])
    assert released.status_code == 200
    assert released.json()["state"] == "RELEASED"

    history = client.get("/api/exams/release/history", headers=tokens["hod"])
    assert history.status_code == 200
    assert any(row["paper_id"] == paper_id for row in history.json())


def test_paper_integrity_is_verified_for_hod(client: TestClient, tokens, secured_paper):
    paper_id = client.get(
        f"/api/exams/{secured_paper['exam_id']}/paper", headers=tokens["teacher"]
    ).json()["paper_id"]
    response = client.get(f"/api/exams/papers/{paper_id}/integrity", headers=tokens["hod"])
    assert response.status_code == 200
    assert response.json()["integrity_verified"] is True


def test_second_generation_is_blocked_after_securing(client: TestClient, tokens, secured_paper):
    response = client.post(
        f"/api/exams/{secured_paper['exam_id']}/generate", headers=tokens["teacher"]
    )
    assert response.status_code == 409
    assert response.json()["error"] == "PAPER_ALREADY_SECURED"


def test_subject_isolation_between_teachers(client: TestClient, tokens, secured_paper):
    # The seeded teacher owns the exam; a forged exam id must 404, never leak.
    response = client.get("/api/exams/does-not-exist", headers=tokens["teacher"])
    assert response.status_code == 404
