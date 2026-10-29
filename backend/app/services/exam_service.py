"""Secure internal examination workflow (teacher requirements -> HOD release)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import constant_time_equal, generate_release_token, sha256_hex
from app.core.timeutils import ensure_utc
from app.encryption.service import KeyManagementError, decrypt_paper
from app.models.enums import (
    ExamStatus,
    GenerationStatus,
    PaperState,
    ReleaseStatus,
    Role,
    Severity,
)
from app.models.tables import (
    EncryptedPaper,
    ExamConfiguration,
    ExamTopic,
    GenerationRequest,
    InternalExam,
    PaperRelease,
    QuestionPaper,
    Subject,
    Topic,
    User,
)
from app.schemas.exam import ExamConfigCreate
from app.services import generation_service, paper_service
from app.services.academic_service import ensure_subject_access
from app.services.audit_service import record_audit, record_security_event
from app.storage.backend import StorageError, get_storage


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _config_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_exam(db: Session, teacher: User, payload: ExamConfigCreate) -> InternalExam:
    subject = ensure_subject_access(db, teacher, payload.subject_id)
    topic_rows = db.execute(
        select(Topic).where(
            Topic.subject_id == subject.id,
            Topic.id.in_([topic.topic_id for topic in payload.topics]),
        )
    ).scalars().all()
    if len(topic_rows) != len(payload.topics):
        raise Conflict("One or more topics do not belong to this subject.", "INVALID_TOPICS")

    exam = InternalExam(
        subject_id=subject.id,
        created_by=teacher.id,
        title=payload.title,
        exam_type=payload.exam_type,
        exam_date=payload.exam_date,
        duration_minutes=payload.duration_minutes,
        total_marks=payload.total_marks,
        status=ExamStatus.DRAFT,
        instructions=payload.instructions,
        venue=payload.venue,
    )
    db.add(exam)
    db.flush()
    for topic in payload.topics:
        db.add(
            ExamTopic(
                exam_id=exam.id,
                topic_id=topic.topic_id,
                weight_percent=topic.weight_percent,
            )
        )
    topic_distribution = {
        str(topic.topic_id): float(topic.weight_percent) for topic in payload.topics
    }
    pattern = [
        {
            "marks": item.marks,
            "count": item.count,
            "question_type": str(item.question_type),
            "label": item.label,
        }
        for item in payload.pattern
    ]
    config = ExamConfiguration(
        exam_id=exam.id,
        total_marks=payload.total_marks,
        pattern=pattern,
        difficulty_distribution={str(k): float(v) for k, v in payload.difficulty_distribution.items()},
        bloom_distribution={str(k): float(v) for k, v in payload.bloom_distribution.items()},
        topic_distribution=topic_distribution,
        instructions=payload.instructions,
        retry_limit=payload.retry_limit,
        frozen=False,
    )
    db.add(config)
    record_audit(
        db,
        event="EXAM_CONFIGURATION_CREATED",
        actor=teacher,
        resource_type="internal_exam",
        resource_id=exam.id,
        metadata={
            "subject_id": subject.id,
            "total_marks": payload.total_marks,
            "questions": sum(item.count for item in payload.pattern),
            "topics": len(payload.topics),
        },
    )
    db.flush()
    return exam


def submit_exam(db: Session, teacher: User, exam_id: str, ip: str = "") -> InternalExam:
    exam = _owned_exam(db, teacher, exam_id)
    config = _config_for(db, exam.id)
    if config.frozen:
        raise Conflict("This configuration is already frozen for generation.", "CONFIG_FROZEN")
    if exam.status != ExamStatus.DRAFT:
        raise Conflict("Only draft examinations can be submitted.", "INVALID_EXAM_STATE")
    if exam.exam_date.replace(tzinfo=timezone.utc) < _now():
        raise Conflict("The examination date must be in the future.", "PAST_EXAM_DATE")
    payload = {
        "exam_id": exam.id,
        "subject_id": exam.subject_id,
        "title": exam.title,
        "exam_type": exam.exam_type,
        "exam_date": ensure_utc(exam.exam_date).isoformat(),
        "duration_minutes": exam.duration_minutes,
        "total_marks": config.total_marks,
        "pattern": config.pattern,
        "difficulty": config.difficulty_distribution,
        "bloom": config.bloom_distribution,
        "topics": config.topic_distribution,
        "retry_limit": config.retry_limit,
    }
    config.frozen = True
    config.frozen_at = _now()
    config.frozen_hash = _config_hash(payload)
    exam.status = ExamStatus.SUBMITTED
    record_audit(
        db,
        event="EXAM_CONFIGURATION_SUBMITTED",
        actor=teacher,
        resource_type="internal_exam",
        resource_id=exam.id,
        ip=ip,
        metadata={"frozen_hash": config.frozen_hash[:16], "retry_limit": config.retry_limit},
    )
    db.flush()
    return exam


def queue_generation(
    db: Session, teacher: User, exam_id: str, ip: str = ""
) -> GenerationRequest:
    exam = _owned_exam(db, teacher, exam_id)
    config = _config_for(db, exam.id)
    if not config.frozen:
        raise Conflict("Submit (freeze) the configuration before requesting generation.", "CONFIG_NOT_FROZEN")
    if exam.status in {ExamStatus.SECURED, ExamStatus.RELEASED, ExamStatus.PROCESSING}:
        raise Conflict("This examination already has a secured paper.", "PAPER_ALREADY_SECURED")
    active = db.execute(
        select(GenerationRequest).where(
            GenerationRequest.exam_id == exam.id,
            GenerationRequest.status.in_([GenerationStatus.QUEUED, GenerationStatus.RUNNING]),
        )
    ).scalar_one_or_none()
    if active:
        raise Conflict("A generation request is already in progress.", "GENERATION_IN_PROGRESS")
    if settings.effective_llm_provider == "mock" and not settings.DEMO_MODE:
        raise Conflict(
            "No real AI provider is configured. Set LLM_API_KEY or enable DEMO_MODE.",
            "AI_NOT_CONFIGURED",
        )
    request = GenerationRequest(
        exam_id=exam.id,
        requested_by=teacher.id,
        status=GenerationStatus.QUEUED,
        provider=settings.effective_llm_provider,
        mode="DEMO" if settings.effective_llm_provider == "mock" else "REAL_AI",
    )
    db.add(request)
    record_audit(
        db,
        event="GENERATION_REQUESTED",
        actor=teacher,
        resource_type="internal_exam",
        resource_id=exam.id,
        ip=ip,
        metadata={"request_id": request.id, "mode": request.mode},
    )
    db.flush()
    return request


def _owned_exam(db: Session, teacher: User, exam_id: str) -> InternalExam:
    exam = db.get(InternalExam, exam_id)
    if exam is None:
        raise NotFound("Examination")
    if teacher.role == Role.HOD:
        return exam
    if exam.created_by != teacher.id:
        raise Forbidden("You can only manage examinations you created.", code="NOT_EXAM_OWNER")
    return exam


def _config_for(db: Session, exam_id: str) -> ExamConfiguration:
    config = db.execute(
        select(ExamConfiguration).where(ExamConfiguration.exam_id == exam_id)
    ).scalar_one_or_none()
    if config is None:
        raise NotFound("Examination configuration")
    return config


def _exam_out(db: Session, exam: InternalExam, *, include_config: bool = True) -> dict[str, Any]:
    subject = db.get(Subject, exam.subject_id)
    creator = db.get(User, exam.created_by)
    topics = db.execute(
        select(Topic.id, Topic.name, Topic.unit, ExamTopic.weight_percent)
        .join(ExamTopic, ExamTopic.topic_id == Topic.id)
        .where(ExamTopic.exam_id == exam.id)
    ).all()
    config = db.execute(
        select(ExamConfiguration).where(ExamConfiguration.exam_id == exam.id)
    ).scalar_one_or_none()
    request = db.execute(
        select(GenerationRequest)
        .where(GenerationRequest.exam_id == exam.id)
        .order_by(GenerationRequest.created_at.desc())
    ).scalars().first()
    paper = db.execute(
        select(QuestionPaper).where(QuestionPaper.exam_id == exam.id).order_by(QuestionPaper.version.desc())
    ).scalars().first()
    result: dict[str, Any] = {
        "id": exam.id,
        "subject_id": exam.subject_id,
        "subject_name": subject.name if subject else "",
        "title": exam.title,
        "exam_type": exam.exam_type,
        "exam_date": exam.exam_date,
        "duration_minutes": exam.duration_minutes,
        "total_marks": exam.total_marks,
        "venue": exam.venue,
        "instructions": exam.instructions,
        "status": exam.status,
        "created_by_name": creator.full_name if creator else "",
        "topics": [
            {"id": topic_id, "name": name, "unit": unit, "weight_percent": weight}
            for topic_id, name, unit, weight in topics
        ],
        "generation_status": request.status if request else None,
        "generation_request_id": request.id if request else None,
        "paper_state": paper.state if paper else None,
        "paper_id": paper.id if paper else None,
        "created_at": exam.created_at,
        "configuration": None,
    }
    if include_config and config:
        result["configuration"] = {
            "total_marks": config.total_marks,
            "pattern": config.pattern,
            "difficulty_distribution": config.difficulty_distribution,
            "bloom_distribution": config.bloom_distribution,
            "topic_distribution": config.topic_distribution,
            "instructions": config.instructions,
            "retry_limit": config.retry_limit,
            "frozen": config.frozen,
            "frozen_at": config.frozen_at,
            "frozen_hash": config.frozen_hash,
        }
    return result


def list_exams(db: Session, actor: User) -> list[dict[str, Any]]:
    query = select(InternalExam)
    if actor.role == Role.TEACHER:
        query = query.where(InternalExam.created_by == actor.id)
    elif actor.role == Role.STUDENT:
        raise Forbidden("Students do not have access to internal examinations.")
    exams = db.execute(query.order_by(InternalExam.exam_date.desc())).scalars().all()
    return [_exam_out(db, exam) for exam in exams]


def get_exam(db: Session, actor: User, exam_id: str) -> dict[str, Any]:
    exam = db.get(InternalExam, exam_id)
    if exam is None:
        raise NotFound("Examination")
    if actor.role == Role.STUDENT:
        raise Forbidden("Students do not have access to internal examinations.")
    if actor.role == Role.TEACHER and exam.created_by != actor.id:
        raise Forbidden("You can only view examinations you created.", code="NOT_EXAM_OWNER")
    return _exam_out(db, exam)


def generation_status(db: Session, actor: User, exam_id: str) -> dict[str, Any]:
    exam = db.get(InternalExam, exam_id)
    if exam is None:
        raise NotFound("Examination")
    if actor.role == Role.STUDENT:
        raise Forbidden("Students do not have access to internal examinations.")
    if actor.role == Role.TEACHER and exam.created_by != actor.id:
        raise Forbidden("You can only view examinations you created.", code="NOT_EXAM_OWNER")
    request = db.execute(
        select(GenerationRequest)
        .where(GenerationRequest.exam_id == exam.id)
        .order_by(GenerationRequest.created_at.desc())
    ).scalars().first()
    if request is None:
        raise NotFound("Generation request")
    return generation_service.request_status(db, request)


def secure_paper_for_teacher(db: Session, actor: User, paper_id: str) -> dict[str, Any]:
    paper = db.get(QuestionPaper, paper_id)
    if paper is None:
        raise NotFound("Paper")
    exam = db.get(InternalExam, paper.exam_id)
    if exam is not None and actor.role == Role.TEACHER and exam.created_by != actor.id:
        raise Forbidden("You can only inspect your own examination paper metadata.", code="NOT_EXAM_OWNER")
    if actor.role == Role.STUDENT:
        raise Forbidden("Students cannot access internal examination papers.")
    return paper_service.paper_for_teacher(db, paper)


def exam_secure_paper(db: Session, actor: User, exam_id: str) -> dict[str, Any]:
    exam = db.get(InternalExam, exam_id)
    if exam is None:
        raise NotFound("Examination")
    if actor.role == Role.STUDENT:
        raise Forbidden("Students cannot access internal examination papers.")
    if actor.role == Role.TEACHER and exam.created_by != actor.id:
        raise Forbidden("You can only inspect your own examination paper metadata.", code="NOT_EXAM_OWNER")
    paper = db.execute(
        select(QuestionPaper).where(QuestionPaper.exam_id == exam.id).order_by(QuestionPaper.version.desc())
    ).scalars().first()
    if paper is None:
        raise NotFound("Secured paper")
    return paper_service.paper_for_teacher(db, paper)


# --- HOD release workflow -------------------------------------------------
def release_window(exam: InternalExam) -> tuple[datetime, datetime]:
    exam_date = ensure_utc(exam.exam_date)
    opens = exam_date - timedelta(minutes=settings.PRINT_EARLY_OPEN_MINUTES)
    closes = exam_date + timedelta(minutes=settings.RELEASE_WINDOW_MINUTES)
    return opens, closes


def _window_state(exam: InternalExam) -> tuple[bool, bool, datetime, datetime]:
    opens, closes = release_window(exam)
    now = _now()
    return opens <= now <= closes, now < opens, opens, closes


def release_queue(db: Session, actor: User) -> list[dict[str, Any]]:
    if actor.role != Role.HOD:
        raise Forbidden("Only the HOD can view the release queue.")
    papers = db.execute(
        select(QuestionPaper).order_by(QuestionPaper.version.desc())
    ).scalars().all()
    items: list[dict[str, Any]] = []
    now = _now()
    for paper in papers:
        if paper.state not in {
            PaperState.ENCRYPTED,
            PaperState.STORED,
            PaperState.RELEASE_PENDING,
            PaperState.DECRYPTED,
            PaperState.PRINTED,
            PaperState.RELEASED,
        }:
            continue
        exam = db.get(InternalExam, paper.exam_id)
        if exam is None:
            continue
        opens, closes = release_window(exam)
        subject = db.get(Subject, exam.subject_id)
        encrypted = db.execute(
            select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)
        ).scalar_one_or_none()
        release = db.execute(
            select(PaperRelease)
            .where(PaperRelease.paper_id == paper.id, PaperRelease.status != ReleaseStatus.EXPIRED)
            .order_by(PaperRelease.authorized_at.desc())
        ).scalars().first()
        items.append(
            {
                "paper_id": paper.id,
                "exam_id": exam.id,
                "exam_title": exam.title,
                "subject_name": subject.name if subject else "",
                "exam_type": exam.exam_type,
                "exam_date": exam.exam_date,
                "duration_minutes": exam.duration_minutes,
                "total_marks": paper.total_marks,
                "venue": exam.venue,
                "paper_state": paper.state,
                "encrypted_at": paper.encrypted_at,
                "window_opens_at": opens,
                "window_closes_at": closes,
                "window_open": opens <= now <= closes,
                "release": (
                    {
                        "id": release.id,
                        "status": release.status,
                        "authorized_at": release.authorized_at,
                        "token_expires_at": release.token_expires_at,
                        "print_count": release.print_count,
                        "reason": release.reason,
                        "authorized_by_name": (
                            db.get(User, release.authorized_by).full_name
                            if db.get(User, release.authorized_by)
                            else ""
                        ),
                        "decrypted_at": release.decrypted_at,
                        "printed_at": release.printed_at,
                    }
                    if release
                    else None
                ),
            }
        )
    items.sort(key=lambda item: ensure_utc(item["exam_date"]))
    return items


def authorize_release(
    db: Session, hod: User, paper_id: str, reason: str, ip: str
) -> dict[str, Any]:
    if hod.role != Role.HOD:
        raise Forbidden("Only the HOD can authorize paper release.")
    paper = db.get(QuestionPaper, paper_id)
    if paper is None:
        raise NotFound("Paper")
    exam = db.get(InternalExam, paper.exam_id)
    if exam is None:
        raise NotFound("Examination")
    open_now, too_early, opens, closes = _window_state(exam)
    if not open_now:
        record_security_event(
            db,
            event_type="RELEASE_OUTSIDE_WINDOW",
            summary=f"Release attempted outside window for paper {paper.id}",
            severity=Severity.WARNING,
            actor_id=hod.id,
            ip=ip,
            detail={"opens": opens.isoformat(), "closes": closes.isoformat()},
        )
        record_audit(
            db,
            event="PAPER_RELEASE_WINDOW_REJECTED",
            actor=hod,
            resource_type="question_paper",
            resource_id=paper.id,
            success=False,
            ip=ip,
            metadata={"too_early": too_early},
        )
        db.commit()
        raise Forbidden(
            "The controlled release window is closed. "
            f"Windows open {settings.PRINT_EARLY_OPEN_MINUTES} minutes before the exam "
            f"and close {settings.RELEASE_WINDOW_MINUTES} minutes after it.",
            code="RELEASE_WINDOW_CLOSED",
        )
    if not hod.mfa_enabled:
        raise Forbidden("MFA must be enrolled before releasing papers.", code="MFA_REQUIRED")

    state = PaperState(paper.state)
    if state in {PaperState.ENCRYPTED, PaperState.STORED}:
        paper_service.transition(
            db,
            paper,
            PaperState.RELEASE_PENDING,
            actor=hod,
            reason=f"Release authorized: {reason}",
            ip=ip,
        )
    elif state not in {PaperState.RELEASE_PENDING, PaperState.DECRYPTED, PaperState.PRINTED}:
        raise Conflict(
            f"Paper in state {paper.state} cannot be released. It must be encrypted and stored.",
            "INVALID_PAPER_STATE",
        )

    token, token_hash = generate_release_token()
    expires_at = _now() + timedelta(minutes=settings.RELEASE_TOKEN_TTL_MINUTES)
    existing = db.execute(
        select(PaperRelease).where(
            PaperRelease.paper_id == paper.id,
            PaperRelease.status.notin_([ReleaseStatus.RELEASED, ReleaseStatus.EXPIRED]),
        )
    ).scalars().all()
    for release in existing:
        release.status = ReleaseStatus.EXPIRED
    release = PaperRelease(
        paper_id=paper.id,
        exam_id=exam.id,
        status=ReleaseStatus.AUTHORIZED,
        release_token_hash=token_hash,
        authorized_by=hod.id,
        token_expires_at=expires_at,
        ip_address=ip,
        reason=reason,
    )
    db.add(release)
    record_audit(
        db,
        event="PAPER_RELEASE_AUTHORIZED",
        actor=hod,
        resource_type="question_paper",
        resource_id=paper.id,
        ip=ip,
        metadata={"reason": reason, "token_ttl_minutes": settings.RELEASE_TOKEN_TTL_MINUTES},
    )
    record_security_event(
        db,
        event_type="PAPER_RELEASE_AUTHORIZED",
        summary=f"HOD authorized release of {exam.title}",
        severity=Severity.INFO,
        actor_id=hod.id,
        ip=ip,
        detail={"paper_id": paper.id, "exam_id": exam.id},
    )
    db.commit()
    return {
        "paper_id": paper.id,
        "release_token": token,
        "expires_at": expires_at,
        "exam_date": exam.exam_date,
        "window_closes_at": closes,
        "message": "Release authorized. The token is single-session and expires shortly.",
    }


def _validate_release(
    db: Session, hod: User, paper_id: str, token: str, ip: str, event: str
) -> tuple[QuestionPaper, InternalExam, PaperRelease, EncryptedPaper]:
    if hod.role != Role.HOD:
        raise Forbidden("Only the HOD can perform this action.")
    paper, encrypted = paper_service.bundle_for(db, paper_id)
    exam = db.get(InternalExam, paper.exam_id)
    if exam is None:
        raise NotFound("Examination")
    open_now, _, opens, closes = _window_state(exam)
    if not open_now:
        record_audit(
            db,
            event=f"{event}_WINDOW_REJECTED",
            actor=hod,
            resource_type="question_paper",
            resource_id=paper.id,
            success=False,
            ip=ip,
        )
        db.commit()
        raise Forbidden(
            "The release window is closed for this examination.",
            code="RELEASE_WINDOW_CLOSED",
        )
    release = db.execute(
        select(PaperRelease)
        .where(PaperRelease.paper_id == paper.id, PaperRelease.status != ReleaseStatus.EXPIRED)
        .order_by(PaperRelease.authorized_at.desc())
    ).scalars().first()
    if release is None or release.status not in {
        ReleaseStatus.AUTHORIZED,
        ReleaseStatus.DECRYPTED,
        ReleaseStatus.PRINTED,
    }:
        record_audit(
            db,
            event=f"{event}_NO_AUTHORIZATION",
            actor=hod,
            resource_type="question_paper",
            resource_id=paper.id,
            success=False,
            ip=ip,
        )
        db.commit()
        raise Forbidden(
            "No active release authorization exists. Authorize the release first.",
            code="RELEASE_NOT_AUTHORIZED",
        )
    if not constant_time_equal(sha256_hex(token), release.release_token_hash):
        record_security_event(
            db,
            event_type="RELEASE_TOKEN_REJECTED",
            summary="Invalid release token presented",
            severity=Severity.WARNING,
            actor_id=hod.id,
            ip=ip,
            detail={"paper_id": paper.id},
        )
        record_audit(
            db,
            event=f"{event}_INVALID_TOKEN",
            actor=hod,
            resource_type="question_paper",
            resource_id=paper.id,
            success=False,
            ip=ip,
        )
        db.commit()
        raise Forbidden("The release token is invalid.", code="INVALID_RELEASE_TOKEN")
    if ensure_utc(release.token_expires_at) < _now():
        raise Forbidden(
            "The release token expired. Authorize the release again.", code="RELEASE_TOKEN_EXPIRED"
        )
    if not hod.mfa_enabled:
        raise Forbidden("MFA is required for this operation.", code="MFA_REQUIRED")
    return paper, exam, release, encrypted


def _load_payload(paper: QuestionPaper, encrypted: EncryptedPaper) -> dict[str, Any]:
    try:
        ciphertext = get_storage().get(encrypted.ciphertext_path)
    except StorageError as exc:
        raise Conflict("Ciphertext is missing from secure storage.", "STORAGE_MISSING") from exc
    try:
        payload, _ = decrypt_paper(
            paper_id=paper.id,
            exam_id=paper.exam_id,
            version=paper.version,
            ciphertext=ciphertext,
            key_id=encrypted.key_id,
            wrapped_dek=encrypted.wrapped_dek,
            payload_nonce=encrypted.payload_nonce,
            expected_sha256=encrypted.ciphertext_sha256,
        )
    except KeyManagementError as exc:
        raise Conflict(str(exc), "DECRYPTION_FAILED") from exc
    return payload


def decrypt_for_release(
    db: Session, hod: User, paper_id: str, token: str, reason: str, ip: str
) -> dict[str, Any]:
    paper, exam, release, encrypted = _validate_release(
        db, hod, paper_id, token, ip, "PAPER_DECRYPT"
    )
    payload = _load_payload(paper, encrypted)
    verified = paper.plaintext_sha256 == encrypted.plaintext_sha256
    if not verified:
        raise Conflict("Plaintext hash mismatch: refusing to decrypt.", "INTEGRITY_FAILURE")
    if PaperState(paper.state) == PaperState.RELEASE_PENDING:
        paper_service.transition(
            db,
            paper,
            PaperState.DECRYPTED,
            actor=hod,
            reason=f"Authorized decryption: {reason}",
            ip=ip,
        )
    release.status = ReleaseStatus.DECRYPTED
    release.decrypted_at = _now()
    record_audit(
        db,
        event="PAPER_DECRYPTED",
        actor=hod,
        resource_type="question_paper",
        resource_id=paper.id,
        ip=ip,
        metadata={
            "reason": reason,
            "exam_id": exam.id,
            "integrity_verified": True,
            "question_count": payload.get("question_count"),
        },
    )
    record_security_event(
        db,
        event_type="PAPER_DECRYPTED",
        summary=f"Paper decrypted for {exam.title}",
        severity=Severity.WARNING,
        actor_id=hod.id,
        ip=ip,
        detail={"paper_id": paper.id, "exam_id": exam.id, "reason": reason},
    )
    db.commit()
    return {
        "paper_id": paper.id,
        "decrypted": True,
        "integrity_verified": verified,
        "question_count": payload.get("question_count", 0),
        "sections": [
            {
                "label": section["label"],
                "questions": len(section["questions"]),
                "marks_per_question": section["marks_per_question"],
            }
            for section in payload.get("sections", [])
        ],
        "plaintext_ttl_seconds": 0,
        "message": (
            "Integrity verified and paper decrypted in memory only. Generate the printable copy "
            "immediately; no plaintext copy is stored."
        ),
    }


def printable_pdf(
    db: Session, hod: User, paper_id: str, token: str, ip: str
) -> tuple[bytes, dict[str, Any]]:
    from app.services.print_service import render_paper_pdf

    paper, exam, release, encrypted = _validate_release(
        db, hod, paper_id, token, ip, "PAPER_PRINT"
    )
    if PaperState(paper.state) not in {PaperState.DECRYPTED, PaperState.PRINTED}:
        raise Conflict("Decrypt the paper before printing.", "PAPER_NOT_DECRYPTED")
    if release.print_count >= 3:
        raise Conflict("The maximum of 3 controlled prints has been reached.", "PRINT_LIMIT_REACHED")
    payload = _load_payload(paper, encrypted)
    pdf_bytes = render_paper_pdf(payload)
    release.print_count += 1
    release.printed_at = _now()
    release.status = ReleaseStatus.PRINTED
    if PaperState(paper.state) == PaperState.DECRYPTED:
        paper_service.transition(
            db,
            paper,
            PaperState.PRINTED,
            actor=hod,
            reason=f"Controlled print #{release.print_count}",
            ip=ip,
        )
    record_audit(
        db,
        event="PAPER_PRINTED",
        actor=hod,
        resource_type="question_paper",
        resource_id=paper.id,
        ip=ip,
        metadata={
            "print_count": release.print_count,
            "bytes": len(pdf_bytes),
            "exam_id": exam.id,
        },
    )
    record_security_event(
        db,
        event_type="PAPER_PRINTED",
        summary=f"Controlled print #{release.print_count} for {exam.title}",
        severity=Severity.INFO,
        actor_id=hod.id,
        ip=ip,
        detail={"paper_id": paper.id},
    )
    db.commit()
    return pdf_bytes, {
        "paper_id": paper.id,
        "printed": True,
        "print_count": release.print_count,
        "prints_remaining": max(0, 3 - release.print_count),
        "message": "Printable copy generated in memory. Store the printed copy securely.",
    }


def mark_released(db: Session, hod: User, paper_id: str, ip: str) -> dict[str, Any]:
    if hod.role != Role.HOD:
        raise Forbidden("Only the HOD can release a paper.")
    paper = db.get(QuestionPaper, paper_id)
    if paper is None:
        raise NotFound("Paper")
    exam = db.get(InternalExam, paper.exam_id)
    if exam is None:
        raise NotFound("Examination")
    if PaperState(paper.state) != PaperState.PRINTED:
        raise Conflict("Print the paper before marking it released.", "PAPER_NOT_PRINTED")
    paper_service.transition(
        db, paper, PaperState.RELEASED, actor=hod, reason="Examination released to invigilation", ip=ip
    )
    release = db.execute(
        select(PaperRelease)
        .where(PaperRelease.paper_id == paper.id)
        .order_by(PaperRelease.authorized_at.desc())
    ).scalars().first()
    if release:
        release.status = ReleaseStatus.RELEASED
        release.released_at = _now()
    exam.status = ExamStatus.RELEASED
    record_audit(
        db,
        event="PAPER_RELEASED",
        actor=hod,
        resource_type="question_paper",
        resource_id=paper.id,
        ip=ip,
        metadata={"exam_id": exam.id, "print_count": release.print_count if release else 0},
    )
    db.commit()
    return {"paper_id": paper.id, "state": paper.state, "message": "Paper marked as released."}


def release_history(db: Session, actor: User) -> list[dict[str, Any]]:
    if actor.role != Role.HOD:
        raise Forbidden("Only the HOD can view release history.")
    releases = db.execute(
        select(PaperRelease).order_by(PaperRelease.authorized_at.desc()).limit(200)
    ).scalars().all()
    rows = []
    for release in releases:
        exam = db.get(InternalExam, release.exam_id)
        subject = db.get(Subject, exam.subject_id) if exam else None
        authorizer = db.get(User, release.authorized_by)
        rows.append(
            {
                "release_id": release.id,
                "paper_id": release.paper_id,
                "exam_title": exam.title if exam else "",
                "subject_name": subject.name if subject else "",
                "status": release.status,
                "authorized_by_name": authorizer.full_name if authorizer else "",
                "authorized_at": release.authorized_at,
                "decrypted_at": release.decrypted_at,
                "printed_at": release.printed_at,
                "print_count": release.print_count,
                "reason": release.reason,
            }
        )
    return rows


def scheduled_exams(db: Session, actor: User) -> list[dict[str, Any]]:
    if actor.role != Role.HOD:
        raise Forbidden("Only the HOD can view the examination schedule.")
    exams = db.execute(select(InternalExam).order_by(InternalExam.exam_date)).scalars().all()
    rows = []
    for exam in exams:
        subject = db.get(Subject, exam.subject_id)
        paper = db.execute(
            select(QuestionPaper).where(QuestionPaper.exam_id == exam.id)
        ).scalars().first()
        open_now, too_early, opens, closes = _window_state(exam)
        rows.append(
            {
                "id": exam.id,
                "title": exam.title,
                "exam_type": exam.exam_type,
                "subject_name": subject.name if subject else "",
                "exam_date": exam.exam_date,
                "duration_minutes": exam.duration_minutes,
                "total_marks": exam.total_marks,
                "venue": exam.venue,
                "status": exam.status,
                "paper_state": paper.state if paper else None,
                "paper_id": paper.id if paper else None,
                "window_opens_at": opens,
                "window_closes_at": closes,
                "window_open": open_now,
                "too_early": too_early,
            }
        )
    return rows
