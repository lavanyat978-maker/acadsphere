"""Paper lifecycle: explicit state machine, lock, AES-256-GCM encryption, storage.

Two kinds of paper share this lifecycle:

* GENERATED - assembled from validated candidates; the encrypted body is the
  canonical JSON payload, rendered to PDF only at print time.
* UPLOADED - a teacher-prepared PDF; the encrypted body is the PDF itself.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, Forbidden, InvalidState, NotFound
from app.encryption.service import decrypt_bytes, encrypt_bytes, km
from app.models.enums import PAPER_TRANSITIONS, ExamStatus, PaperState
from app.models.tables import (
    EncryptedPaper,
    InternalExam,
    PaperStateTransition,
    QuestionPaper,
    QuestionPool,
    User,
)
from app.services.audit_service import record_audit
from app.storage.backend import StorageError, get_storage, paper_key

logger = logging.getLogger("praxis.paper")

PDF_CONTENT_TYPE = "application/pdf"
JSON_CONTENT_TYPE = "application/json"

# States in which a paper has a ciphertext and has not been opened for release.
SECURED_STATES = {PaperState.ENCRYPTED, PaperState.STORED}
# States after which a paper is part of an active or finished release.
RELEASE_STATES = {
    PaperState.RELEASE_PENDING,
    PaperState.DECRYPTED,
    PaperState.PRINTED,
    PaperState.RELEASED,
}

AUDIT_EVENT_FOR_STATE = {
    PaperState.SUBMITTED: "PAPER_SUBMITTED",
    PaperState.GENERATING: "PAPER_GENERATION_STARTED",
    PaperState.VALIDATING: "PAPER_VALIDATION_STARTED",
    PaperState.ASSEMBLED: "PAPER_ASSEMBLED",
    PaperState.VALIDATED: "PAPER_FINAL_VALIDATION_PASSED",
    PaperState.LOCKED: "PAPER_LOCKED",
    PaperState.ENCRYPTED: "PAPER_ENCRYPTED",
    PaperState.STORED: "PAPER_STORED",
    PaperState.RELEASE_PENDING: "PAPER_RELEASE_AUTHORIZED",
    PaperState.DECRYPTED: "PAPER_DECRYPTED",
    PaperState.PRINTED: "PAPER_PRINTED",
    PaperState.RELEASED: "PAPER_RELEASED",
    PaperState.DISCARDED: "PAPER_DISCARDED",
}


def transition(
    db: Session,
    paper: QuestionPaper,
    target: PaperState,
    *,
    actor: User | None = None,
    actor_role: str = "SYSTEM",
    reason: str = "",
    ip: str = "",
) -> None:
    current = PaperState(paper.state)
    allowed = PAPER_TRANSITIONS.get(current, set())
    if target not in allowed:
        record_audit(
            db,
            event="ILLEGAL_STATE_TRANSITION_BLOCKED",
            actor=actor,
            actor_role=actor_role,
            resource_type="question_paper",
            resource_id=paper.id,
            success=False,
            ip=ip,
            metadata={"from": current, "to": target},
        )
        raise InvalidState(
            f"Cannot move paper from {current} to {target}. "
            f"Allowed next states: {', '.join(sorted(s.value for s in allowed)) or 'none'}."
        )
    paper.state = target
    if target == PaperState.LOCKED:
        paper.locked_at = datetime.now(timezone.utc)
    if target == PaperState.ENCRYPTED:
        paper.encrypted_at = datetime.now(timezone.utc)
    db.add(
        PaperStateTransition(
            paper_id=paper.id,
            from_state=current,
            to_state=target,
            actor_id=actor.id if actor else None,
            actor_role=actor.role if actor else actor_role,
            reason=reason[:255],
        )
    )
    record_audit(
        db,
        event=AUDIT_EVENT_FOR_STATE.get(target, "PAPER_STATE_CHANGED"),
        actor=actor,
        actor_role=actor_role,
        resource_type="question_paper",
        resource_id=paper.id,
        ip=ip,
        metadata={"from": current, "to": target, "reason": reason},
    )
    db.flush()


def latest_paper(db: Session, exam_id: str) -> QuestionPaper | None:
    return (
        db.execute(
            select(QuestionPaper).where(QuestionPaper.exam_id == exam_id).order_by(QuestionPaper.version.desc())
        )
        .scalars()
        .first()
    )


def next_version(db: Session, exam_id: str) -> int:
    current = db.execute(
        select(func.max(QuestionPaper.version)).where(QuestionPaper.exam_id == exam_id)
    ).scalar_one_or_none()
    return int(current or 0) + 1


def create_paper(
    db: Session,
    *,
    exam_id: str,
    generation_request_id: str | None,
    version: int | None = None,
    actor: User | None = None,
    reason: str = "Paper record created for secure generation",
) -> QuestionPaper:
    paper = QuestionPaper(
        exam_id=exam_id,
        generation_request_id=generation_request_id,
        version=version or next_version(db, exam_id),
        state=PaperState.DRAFT,
    )
    db.add(paper)
    db.flush()
    transition(db, paper, PaperState.SUBMITTED, actor=actor, reason=reason)
    return paper


def lock_paper(db: Session, paper: QuestionPaper, actor: User | None, *, ip: str = "") -> None:
    if PaperState(paper.state) != PaperState.VALIDATED:
        raise InvalidState("Only a fully validated paper can be locked.")
    if not paper.validation_summary or not paper.validation_summary.get("passed"):
        raise InvalidState("Final validation must pass before locking.")
    transition(db, paper, PaperState.LOCKED, actor=actor, reason="Immutable final paper locked", ip=ip)


def seal_question_pool(db: Session, exam_id: str) -> None:
    """Drop plaintext for every candidate of an exam, selected or not.

    Unselected candidates are often near-duplicates of the real questions, so
    they are as sensitive as the paper itself. Embeddings stay so duplicate
    detection keeps working on a regeneration.
    """
    db.execute(
        update(QuestionPool)
        .where(QuestionPool.exam_id == exam_id, QuestionPool.question_text.is_not(None))
        .values(sealed=True, question_text=None, expected_answer="")
    )


def encrypt_and_store(
    db: Session,
    paper: QuestionPaper,
    payload: dict[str, Any] | None = None,
    *,
    raw: bytes | None = None,
    content_type: str = JSON_CONTENT_TYPE,
    original_name: str = "",
    page_count: int = 0,
    actor: User | None = None,
    ip: str = "",
    seal: bool = True,
) -> EncryptedPaper:
    """Encrypt the paper body, store the ciphertext, then mark ENCRYPTED/STORED.

    Ordering matters: the ciphertext must exist and read back identically
    before the paper may move to ENCRYPTED, and the question pool plaintext is
    sealed only after the ciphertext is durably stored.
    """
    if PaperState(paper.state) != PaperState.LOCKED:
        raise InvalidState("Paper must be LOCKED before encryption.")
    if (payload is None) == (raw is None):
        raise ValueError("Provide exactly one of payload or raw")

    plaintext = raw if raw is not None else json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    result = encrypt_bytes(paper_id=paper.id, exam_id=paper.exam_id, version=paper.version, plaintext=plaintext)
    key = paper_key(paper.exam_id, paper.id, paper.version)
    try:
        get_storage().put(key, result.ciphertext, "application/octet-stream")
        stored = get_storage().get(key)
    except StorageError as exc:
        record_audit(
            db,
            event="PAPER_ENCRYPTION_FAILED",
            actor=actor,
            actor_role=actor.role if actor else "SYSTEM",
            resource_type="question_paper",
            resource_id=paper.id,
            success=False,
            ip=ip,
            metadata={"reason": "storage_failure"},
        )
        db.flush()
        raise Conflict("Encrypted storage is unavailable. Paper remains locked.", "STORAGE_UNAVAILABLE") from exc

    if stored != result.ciphertext:
        raise Conflict("Stored ciphertext failed read-back verification.", "STORAGE_INTEGRITY")

    encrypted = EncryptedPaper(
        paper_id=paper.id,
        exam_id=paper.exam_id,
        algorithm=result.algorithm,
        key_id=result.key_id,
        wrapped_dek=result.wrapped_dek,
        payload_nonce=result.payload_nonce,
        ciphertext_path=key,
        ciphertext_sha256=result.ciphertext_sha256,
        plaintext_sha256=result.plaintext_sha256,
        ciphertext_bytes=len(result.ciphertext),
        aad=result.aad,
        content_type=content_type,
        original_name=original_name[:255],
        page_count=page_count,
    )
    db.add(encrypted)
    paper.plaintext_sha256 = result.plaintext_sha256
    db.flush()

    transition(db, paper, PaperState.ENCRYPTED, actor=actor, reason="AES-256-GCM envelope encryption", ip=ip)
    transition(db, paper, PaperState.STORED, actor=actor, reason="Ciphertext persisted to object storage", ip=ip)

    if seal:
        seal_question_pool(db, paper.exam_id)
    del plaintext
    logger.info("paper secured", extra={"event": "paper_encrypted", "user_id": actor.id if actor else None})
    return encrypted


def discard_paper(
    db: Session,
    paper: QuestionPaper,
    *,
    actor: User | None = None,
    reason: str,
    ip: str = "",
) -> None:
    """Retire a paper version for good and destroy its ciphertext."""
    if PaperState(paper.state) == PaperState.DISCARDED:
        return
    transition(db, paper, PaperState.DISCARDED, actor=actor, reason=reason, ip=ip)
    bundle = db.execute(select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)).scalar_one_or_none()
    if bundle is not None:
        try:
            get_storage().delete(bundle.ciphertext_path)
        except Exception:  # noqa: BLE001 - the state change is what matters; log for cleanup
            logger.warning("ciphertext delete failed", extra={"event": "ciphertext_delete_failed"})
    seal_question_pool(db, paper.exam_id)


def bundle_for(db: Session, paper_id: str) -> tuple[QuestionPaper, EncryptedPaper]:
    paper = db.get(QuestionPaper, paper_id)
    if paper is None:
        raise NotFound("Paper")
    if PaperState(paper.state) == PaperState.DISCARDED:
        raise Conflict("This paper version was discarded.", "PAPER_DISCARDED")
    bundle = db.execute(select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)).scalar_one_or_none()
    if bundle is None:
        raise Conflict("Paper has no encrypted artifact.", "NO_CIPHERTEXT")
    return paper, bundle


def decrypt_body(paper: QuestionPaper, bundle: EncryptedPaper) -> bytes:
    from app.encryption.service import KeyManagementError

    try:
        ciphertext = get_storage().get(bundle.ciphertext_path)
    except StorageError as exc:
        raise Conflict("Ciphertext is missing from secure storage.", "STORAGE_MISSING") from exc
    try:
        return decrypt_bytes(
            paper_id=paper.id,
            exam_id=paper.exam_id,
            version=paper.version,
            ciphertext=ciphertext,
            key_id=bundle.key_id,
            wrapped_dek=bundle.wrapped_dek,
            payload_nonce=bundle.payload_nonce,
            expected_sha256=bundle.ciphertext_sha256,
        )
    except KeyManagementError as exc:
        raise Conflict(str(exc), "DECRYPTION_FAILED") from exc


def describe_body(bundle: EncryptedPaper, body: bytes) -> dict[str, Any]:
    """Counts safe to show the HOD after decryption: never question text."""
    if bundle.content_type == PDF_CONTENT_TYPE:
        return {"question_count": 0, "page_count": bundle.page_count, "sections": []}
    payload = json.loads(body)
    return {
        "question_count": payload.get("question_count", 0),
        "page_count": 0,
        "sections": [
            {
                "label": section["label"],
                "count": len(section["questions"]),
                "questions": len(section["questions"]),
                "marks_per_question": section["marks_per_question"],
            }
            for section in payload.get("sections", [])
        ],
    }


def verify_integrity(db: Session, paper_id: str) -> dict[str, Any]:
    paper, bundle = bundle_for(db, paper_id)
    body = decrypt_body(paper, bundle)
    from app.core.security import sha256_hex

    matches = paper.plaintext_sha256 == bundle.plaintext_sha256 == sha256_hex(body)
    summary = describe_body(bundle, body)
    del body
    return {
        "paper_id": paper.id,
        "state": paper.state,
        "algorithm": bundle.algorithm,
        "key_id": bundle.key_id,
        "known_key_ids": km.known_key_ids(),
        "ciphertext_bytes": bundle.ciphertext_bytes,
        "content_type": bundle.content_type,
        "plaintext_hash_matches": matches,
        "integrity_verified": matches,
        "question_count": summary["question_count"],
        "page_count": summary["page_count"],
        "sections": summary["sections"],
    }


def paper_for_teacher(db: Session, paper: QuestionPaper) -> dict[str, Any]:
    """Metadata-only view safe to show staff (never question content)."""
    encrypted = db.execute(select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)).scalar_one_or_none()
    transitions = (
        db.execute(
            select(PaperStateTransition)
            .where(PaperStateTransition.paper_id == paper.id)
            .order_by(PaperStateTransition.created_at)
        )
        .scalars()
        .all()
    )
    return {
        "paper_id": paper.id,
        "exam_id": paper.exam_id,
        "version": paper.version,
        "state": paper.state,
        "total_marks": paper.total_marks,
        "question_count": paper.question_count,
        "algorithm": encrypted.algorithm if encrypted else "AES-256-GCM",
        "key_id": encrypted.key_id if encrypted else "",
        "ciphertext_sha256": encrypted.ciphertext_sha256 if encrypted else "",
        "ciphertext_bytes": encrypted.ciphertext_bytes if encrypted else 0,
        "content_type": encrypted.content_type if encrypted else "",
        "original_name": encrypted.original_name if encrypted else "",
        "page_count": encrypted.page_count if encrypted else 0,
        "encrypted_at": paper.encrypted_at,
        "locked_at": paper.locked_at,
        "plaintext_hash_matches": bool(encrypted and encrypted.plaintext_sha256 == paper.plaintext_sha256),
        "transitions": [
            {
                "from": row.from_state,
                "to": row.to_state,
                "at": row.created_at,
                "actor_role": row.actor_role,
                "reason": row.reason,
            }
            for row in transitions
        ],
    }


def deny_plaintext_access(db: Session, *, actor: User, resource: str, ip: str, resource_id: str = "") -> None:
    """Called by routes that must exist but must never serve plaintext to staff/students."""
    record_audit(
        db,
        event="PLAINTEXT_PAPER_ACCESS_DENIED",
        actor=actor,
        resource_type=resource,
        resource_id=resource_id,
        success=False,
        ip=ip,
        metadata={"policy": "internal paper plaintext is HOD-release only"},
    )
    db.commit()
    raise Forbidden(
        "The internal examination paper is sealed. Only the HOD can authorize and print it "
        "during the release window.",
        code="PAPER_SEALED",
    )


def mark_exam_secured(db: Session, exam_id: str) -> None:
    exam = db.get(InternalExam, exam_id)
    if exam:
        exam.status = ExamStatus.SECURED


# --- Uploaded papers ------------------------------------------------------
def inspect_pdf(data: bytes, max_mb: int) -> int:
    """Validate an uploaded paper and return its page count."""
    if not data:
        raise AppError("PAPER_EMPTY", "The uploaded file is empty.", 400)
    if len(data) > max_mb * 1024 * 1024:
        raise AppError("PAPER_TOO_LARGE", f"The paper exceeds the {max_mb} MB limit.", 400)
    if not data.startswith(b"%PDF-"):
        raise AppError("PAPER_NOT_PDF", "Upload the question paper as a PDF file.", 400)
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise AppError(
                "PAPER_PDF_ENCRYPTED",
                "Remove the password from the PDF before uploading; Praxis encrypts it itself.",
                400,
            )
        pages = len(reader.pages)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError("PAPER_PDF_INVALID", "The PDF could not be read. Re-export it and try again.", 400) from exc
    if pages < 1:
        raise AppError("PAPER_PDF_INVALID", "The PDF has no pages.", 400)
    return pages
