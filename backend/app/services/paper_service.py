"""Paper lifecycle: explicit state machine, lock, AES-256-GCM encryption, storage."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, InvalidState, NotFound
from app.encryption.service import decrypt_paper, encrypt_paper, km
from app.models.enums import PAPER_TRANSITIONS, ExamStatus, PaperState, Role
from app.models.tables import (
    EncryptedPaper,
    PaperStateTransition,
    QuestionPaper,
    QuestionPool,
    User,
)
from app.services.audit_service import record_audit
from app.storage.backend import StorageError, get_storage, paper_key

logger = logging.getLogger("praxis.paper")

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


def create_paper(db: Session, *, exam_id: str, generation_request_id: str | None, version: int) -> QuestionPaper:
    paper = QuestionPaper(
        exam_id=exam_id,
        generation_request_id=generation_request_id,
        version=version,
        state=PaperState.DRAFT,
    )
    db.add(paper)
    db.flush()
    transition(db, paper, PaperState.SUBMITTED, reason="Paper record created for secure generation")
    return paper


def lock_paper(db: Session, paper: QuestionPaper, actor: User | None, *, ip: str = "") -> None:
    if PaperState(paper.state) != PaperState.VALIDATED:
        raise InvalidState("Only a fully validated paper can be locked.")
    if not paper.validation_summary or not paper.validation_summary.get("passed"):
        raise InvalidState("Final validation must pass before locking.")
    transition(db, paper, PaperState.LOCKED, actor=actor, reason="Immutable final paper locked", ip=ip)


def encrypt_and_store(
    db: Session,
    paper: QuestionPaper,
    payload: dict[str, Any],
    *,
    actor: User | None = None,
    ip: str = "",
    seal: bool = True,
) -> EncryptedPaper:
    """Encrypt the rendered paper, store the ciphertext, then mark ENCRYPTED/STORED.

    Ordering matters: the ciphertext must exist and hash-verify before the paper
    is allowed to move to ENCRYPTED, and the question pool plaintext is sealed
    only after the ciphertext is durably stored.
    """
    if PaperState(paper.state) != PaperState.LOCKED:
        raise InvalidState("Paper must be LOCKED before encryption.")

    plaintext_bytes = json.dumps(payload, sort_keys=True).encode()
    result = encrypt_paper(
        paper_id=paper.id, exam_id=paper.exam_id, version=paper.version, payload=payload
    )
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
    )
    db.add(encrypted)
    paper.plaintext_sha256 = result.plaintext_sha256
    db.flush()

    transition(db, paper, PaperState.ENCRYPTED, actor=actor, reason="AES-256-GCM envelope encryption", ip=ip)
    transition(db, paper, PaperState.STORED, actor=actor, reason="Ciphertext persisted to object storage", ip=ip)

    if seal:
        db.execute(
            update(QuestionPool)
            .where(QuestionPool.exam_id == paper.exam_id, QuestionPool.selected.is_(True))
            .values(sealed=True, question_text=None, expected_answer="")
        )
    del plaintext_bytes
    logger.info(
        "paper secured",
        extra={"event": "paper_encrypted", "user_id": actor.id if actor else None},
    )
    return encrypted


def bundle_for(db: Session, paper_id: str) -> tuple[QuestionPaper, EncryptedPaper]:
    paper = db.get(QuestionPaper, paper_id)
    if paper is None:
        raise NotFound("Paper")
    bundle = db.execute(select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)).scalar_one_or_none()
    if bundle is None:
        raise Conflict("Paper has no encrypted artifact.", "NO_CIPHERTEXT")
    return paper, bundle


def verify_integrity(db: Session, paper_id: str) -> dict[str, Any]:
    paper, bundle = bundle_for(db, paper_id)
    try:
        ciphertext = get_storage().get(bundle.ciphertext_path)
    except StorageError as exc:
        raise Conflict("Ciphertext is missing from secure storage.", "STORAGE_MISSING") from exc
    payload, verified = decrypt_paper(
        paper_id=paper.id,
        exam_id=paper.exam_id,
        version=paper.version,
        ciphertext=ciphertext,
        key_id=bundle.key_id,
        wrapped_dek=bundle.wrapped_dek,
        payload_nonce=bundle.payload_nonce,
        expected_sha256=bundle.ciphertext_sha256,
    )
    matches = paper.plaintext_sha256 == bundle.plaintext_sha256
    return {
        "paper_id": paper.id,
        "state": paper.state,
        "algorithm": bundle.algorithm,
        "key_id": bundle.key_id,
        "known_key_ids": km.known_key_ids(),
        "ciphertext_bytes": bundle.ciphertext_bytes,
        "plaintext_hash_matches": matches,
        "integrity_verified": verified and matches,
        "question_count": payload.get("question_count", 0),
        "sections": [
            {
                "label": section["label"],
                "count": len(section["questions"]),
                "marks_per_question": section["marks_per_question"],
            }
            for section in payload.get("sections", [])
        ],
    }


def paper_for_teacher(db: Session, paper: QuestionPaper) -> dict[str, Any]:
    """Metadata-only view safe to show teachers (never question content)."""
    encrypted = db.execute(
        select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)
    ).scalar_one_or_none()
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
        "encrypted_at": paper.encrypted_at,
        "locked_at": paper.locked_at,
        "plaintext_hash_matches": bool(
            encrypted and encrypted.plaintext_sha256 == paper.plaintext_sha256
        ),
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
    from app.models.tables import InternalExam

    exam = db.get(InternalExam, exam_id)
    if exam:
        exam.status = ExamStatus.SECURED
