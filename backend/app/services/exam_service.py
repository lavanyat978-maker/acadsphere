"""Secure internal examination workflow (staff blueprint -> sealed paper -> HOD release).

Each exam has one source, chosen at creation:

* GENERATED - the blueprint is frozen and the generation pipeline assembles,
  validates, locks and encrypts the paper. Nobody reads it before release.
* UPLOADED - the teacher uploads a finished PDF. It is locked and encrypted on
  receipt and follows the same HOD release flow. Its author already has the
  content, so the guarantee is "nobody *else* can read it before release".

A failed generation, a replaced upload or a cancelled exam retires the paper
version (DISCARDED) and destroys its ciphertext; a retry always creates a new
version, so no paper is ever stuck in an intermediate state.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import constant_time_equal, generate_release_token, sha256_hex
from app.core.timeutils import ensure_utc
from app.models.enums import (
    PRINT_LIMIT_PER_PAPER,
    ExamSource,
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
from app.services.access import (
    can_edit_exam,
    ensure_edit_exam,
    ensure_subject_staff,
    ensure_view_exam,
    teacher_subject_ids,
)
from app.services.audit_service import record_audit, record_security_event

# Exam states in which nothing may change any more.
CLOSED_EXAM_STATES = {ExamStatus.RELEASED, ExamStatus.CANCELLED}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _config_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_topics(db: Session, subject_id: str, payload: ExamConfigCreate) -> None:
    if not payload.topics:
        return
    topic_rows = db.execute(
        select(Topic.id).where(
            Topic.subject_id == subject_id,
            Topic.id.in_([topic.topic_id for topic in payload.topics]),
        )
    ).all()
    if len(topic_rows) != len(payload.topics):
        raise Conflict("One or more topics do not belong to this subject.", "INVALID_TOPICS")


def _write_blueprint(db: Session, exam: InternalExam, payload: ExamConfigCreate) -> ExamConfiguration:
    for link in list(exam.topics):
        db.delete(link)
    db.flush()
    for topic in payload.topics:
        db.add(ExamTopic(exam_id=exam.id, topic_id=topic.topic_id, weight_percent=topic.weight_percent))
    config = db.execute(select(ExamConfiguration).where(ExamConfiguration.exam_id == exam.id)).scalar_one_or_none()
    if config is None:
        config = ExamConfiguration(exam_id=exam.id, total_marks=payload.total_marks, frozen=False)
        db.add(config)
    config.total_marks = payload.total_marks
    config.pattern = [
        {"marks": item.marks, "count": item.count, "question_type": str(item.question_type), "label": item.label}
        for item in payload.pattern
    ]
    config.difficulty_distribution = {str(k): float(v) for k, v in payload.difficulty_distribution.items()}
    config.bloom_distribution = {str(k): float(v) for k, v in payload.bloom_distribution.items()}
    config.topic_distribution = {str(topic.topic_id): float(topic.weight_percent) for topic in payload.topics}
    config.instructions = payload.instructions
    config.retry_limit = payload.retry_limit
    db.flush()
    return config


def create_exam(db: Session, actor: User, payload: ExamConfigCreate) -> InternalExam:
    subject = ensure_subject_staff(db, actor, payload.subject_id)
    _validate_topics(db, subject.id, payload)
    exam = InternalExam(
        subject_id=subject.id,
        created_by=actor.id,
        title=payload.title,
        exam_type=payload.exam_type,
        exam_date=payload.exam_date,
        duration_minutes=payload.duration_minutes,
        total_marks=payload.total_marks,
        status=ExamStatus.DRAFT,
        instructions=payload.instructions,
        venue=payload.venue,
        source=payload.source,
    )
    db.add(exam)
    db.flush()
    _write_blueprint(db, exam, payload)
    record_audit(
        db,
        event="EXAM_CONFIGURATION_CREATED",
        actor=actor,
        resource_type="internal_exam",
        resource_id=exam.id,
        metadata={
            "subject_id": subject.id,
            "source": payload.source,
            "total_marks": payload.total_marks,
            "question_slots": sum(item.count for item in payload.pattern),
            "topics": len(payload.topics),
        },
    )
    db.flush()
    return exam


def update_exam(db: Session, actor: User, exam_id: str, payload: ExamConfigCreate, ip: str = "") -> InternalExam:
    """Replace a draft blueprint. The source and subject are fixed once created."""
    exam = ensure_edit_exam(db, actor, exam_id)
    if exam.status != ExamStatus.DRAFT:
        raise Conflict("Only draft examinations can be edited.", "INVALID_EXAM_STATE")
    if payload.source != exam.source:
        raise Conflict("The paper source cannot change after creation. Create a new examination.", "SOURCE_FIXED")
    if payload.subject_id != exam.subject_id:
        raise Conflict("The subject cannot change after creation. Create a new examination.", "SUBJECT_FIXED")
    _validate_topics(db, exam.subject_id, payload)
    for field in ("title", "exam_type", "exam_date", "duration_minutes", "total_marks", "instructions", "venue"):
        setattr(exam, field, getattr(payload, field))
    db.refresh(exam, attribute_names=["topics"])
    _write_blueprint(db, exam, payload)
    record_audit(
        db,
        event="EXAM_CONFIGURATION_UPDATED",
        actor=actor,
        resource_type="internal_exam",
        resource_id=exam.id,
        ip=ip,
        metadata={"total_marks": payload.total_marks, "topics": len(payload.topics)},
    )
    db.flush()
    return exam


def _freeze(db: Session, exam: InternalExam, config: ExamConfiguration) -> None:
    payload = {
        "exam_id": exam.id,
        "subject_id": exam.subject_id,
        "source": exam.source,
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


def submit_exam(db: Session, actor: User, exam_id: str, ip: str = "") -> InternalExam:
    exam = ensure_edit_exam(db, actor, exam_id)
    if exam.source != ExamSource.GENERATED:
        raise Conflict("Uploaded-paper examinations are frozen by uploading the paper.", "NOT_GENERATED_EXAM")
    config = _config_for(db, exam.id)
    if config.frozen:
        raise Conflict("This configuration is already frozen for generation.", "CONFIG_FROZEN")
    if exam.status != ExamStatus.DRAFT:
        raise Conflict("Only draft examinations can be submitted.", "INVALID_EXAM_STATE")
    if ensure_utc(exam.exam_date) < _now():
        raise Conflict("The examination date must be in the future.", "PAST_EXAM_DATE")
    _freeze(db, exam, config)
    exam.status = ExamStatus.SUBMITTED
    record_audit(
        db,
        event="EXAM_CONFIGURATION_SUBMITTED",
        actor=actor,
        resource_type="internal_exam",
        resource_id=exam.id,
        ip=ip,
        metadata={"frozen_hash": config.frozen_hash[:16], "retry_limit": config.retry_limit},
    )
    db.flush()
    return exam


def _expire_stale_requests(db: Session, exam_id: str) -> None:
    """A QUEUED/RUNNING request that stopped making progress is marked FAILED."""
    cutoff = _now() - timedelta(minutes=settings.GEN_STALE_MINUTES)
    for request in db.execute(
        select(GenerationRequest).where(
            GenerationRequest.exam_id == exam_id,
            GenerationRequest.status.in_([GenerationStatus.QUEUED, GenerationStatus.RUNNING]),
        )
    ).scalars():
        if ensure_utc(request.updated_at) < cutoff:
            generation_service.mark_failed(db, request, "Generation stopped responding and was abandoned.")


def queue_generation(db: Session, actor: User, exam_id: str, ip: str = "") -> GenerationRequest:
    exam = ensure_edit_exam(db, actor, exam_id)
    if exam.source != ExamSource.GENERATED:
        raise Conflict("This examination uses an uploaded paper. Upload the PDF instead.", "NOT_GENERATED_EXAM")
    config = _config_for(db, exam.id)
    if not config.frozen:
        raise Conflict("Submit (freeze) the configuration before requesting generation.", "CONFIG_NOT_FROZEN")
    if exam.status in CLOSED_EXAM_STATES or exam.status == ExamStatus.SECURED:
        raise Conflict("This examination already has a secured paper.", "PAPER_ALREADY_SECURED")
    if ensure_utc(exam.exam_date) < _now():
        raise Conflict("The examination date has passed.", "PAST_EXAM_DATE")
    _expire_stale_requests(db, exam.id)
    active = db.execute(
        select(GenerationRequest.id).where(
            GenerationRequest.exam_id == exam.id,
            GenerationRequest.status.in_([GenerationStatus.QUEUED, GenerationStatus.RUNNING]),
        )
    ).first()
    if active:
        raise Conflict("A generation request is already in progress.", "GENERATION_IN_PROGRESS")
    from app.rag.pipeline import subject_has_documents

    if not subject_has_documents(db, exam.subject_id):
        raise Conflict(
            "Upload syllabus material for this subject first; questions are generated only from it.",
            "NO_SYLLABUS",
        )
    if settings.effective_llm_provider == "mock" and not settings.DEMO_MODE:
        raise Conflict(
            "No real AI provider is configured. Set LLM_API_KEY or enable DEMO_MODE.",
            "AI_NOT_CONFIGURED",
        )
    retry = exam.status == ExamStatus.FAILED
    exam.status = ExamStatus.SUBMITTED
    request = GenerationRequest(
        exam_id=exam.id,
        requested_by=actor.id,
        status=GenerationStatus.QUEUED,
        provider=settings.effective_llm_provider,
        mode="DEMO" if settings.effective_llm_provider == "mock" else "REAL_AI",
    )
    db.add(request)
    db.flush()
    record_audit(
        db,
        event="GENERATION_REQUESTED",
        actor=actor,
        resource_type="internal_exam",
        resource_id=exam.id,
        ip=ip,
        metadata={"request_id": request.id, "mode": request.mode, "retry": retry},
    )
    db.flush()
    return request


def window_state(exam: InternalExam) -> tuple[bool, bool, datetime, datetime]:
    opens, closes = release_window(exam)
    now = _now()
    return opens <= now <= closes, now < opens, opens, closes


def upload_paper(
    db: Session,
    actor: User,
    exam_id: str,
    *,
    filename: str,
    data: bytes,
    ip: str = "",
) -> InternalExam:
    """Lock, encrypt and store a teacher-prepared PDF as the exam's paper."""
    exam = ensure_edit_exam(db, actor, exam_id)
    if exam.source != ExamSource.UPLOADED:
        raise Conflict("This examination's paper is AI-generated, not uploaded.", "NOT_UPLOADED_EXAM")
    if exam.status in CLOSED_EXAM_STATES:
        raise Conflict(f"This examination is {exam.status.lower()}.", "INVALID_EXAM_STATE")
    if ensure_utc(exam.exam_date) < _now():
        raise Conflict("The examination date has passed.", "PAST_EXAM_DATE")
    _, too_early, opens, _ = window_state(exam)
    if not too_early:
        raise Conflict(
            "The release window has opened, so the paper is frozen. Ask the HOD if it must change.",
            "PAPER_FROZEN",
        )
    pages = paper_service.inspect_pdf(data, settings.MAX_PAPER_UPLOAD_MB)

    previous = paper_service.latest_paper(db, exam.id)
    replaced = False
    if previous is not None and PaperState(previous.state) != PaperState.DISCARDED:
        if PaperState(previous.state) in paper_service.RELEASE_STATES:
            raise Conflict("The paper is already in the release process.", "PAPER_IN_RELEASE")
        paper_service.discard_paper(db, previous, actor=actor, reason="Replaced by a new upload", ip=ip)
        replaced = True

    config = _config_for(db, exam.id)
    if not config.frozen:
        _freeze(db, exam, config)
    paper = paper_service.create_paper(
        db,
        exam_id=exam.id,
        generation_request_id=None,
        actor=actor,
        reason="Prepared paper uploaded",
    )
    paper.total_marks = exam.total_marks
    paper.question_count = sum(int(item.get("count", 0)) for item in config.pattern or [])
    paper.validation_summary = {
        "passed": True,
        "source": ExamSource.UPLOADED,
        "note": "Teacher-prepared paper. Automated question validation does not apply.",
        "validated_at": _now().isoformat(),
    }
    paper_service.transition(db, paper, PaperState.LOCKED, actor=actor, reason="Uploaded paper locked on receipt", ip=ip)
    paper_service.encrypt_and_store(
        db,
        paper,
        raw=data,
        content_type=paper_service.PDF_CONTENT_TYPE,
        original_name=filename,
        page_count=pages,
        actor=actor,
        ip=ip,
        seal=False,
    )
    del data
    exam.status = ExamStatus.SECURED
    record_audit(
        db,
        event="PAPER_UPLOAD_REPLACED" if replaced else "PAPER_UPLOADED",
        actor=actor,
        resource_type="internal_exam",
        resource_id=exam.id,
        ip=ip,
        metadata={"paper_id": paper.id, "version": paper.version, "pages": pages, "filename": filename[:120]},
    )
    if actor.role == Role.HOD:
        _flag_hod_self_handling(db, actor, exam, ip, "uploaded")
    db.flush()
    return exam


def cancel_exam(db: Session, actor: User, exam_id: str, reason: str, ip: str = "") -> InternalExam:
    exam = ensure_edit_exam(db, actor, exam_id)
    if exam.status in CLOSED_EXAM_STATES:
        raise Conflict(f"This examination is already {exam.status.lower()}.", "INVALID_EXAM_STATE")
    paper = paper_service.latest_paper(db, exam.id)
    if paper is not None and PaperState(paper.state) in {PaperState.DECRYPTED, PaperState.PRINTED}:
        raise Conflict(
            "The paper has already been decrypted for printing. Complete or report the release instead.",
            "PAPER_IN_RELEASE",
        )
    for request in db.execute(
        select(GenerationRequest).where(
            GenerationRequest.exam_id == exam.id,
            GenerationRequest.status.in_([GenerationStatus.QUEUED, GenerationStatus.RUNNING]),
        )
    ).scalars():
        generation_service.mark_failed(db, request, "Examination cancelled.", update_exam=False)
    if paper is not None:
        paper_service.discard_paper(db, paper, actor=actor, reason=f"Examination cancelled: {reason}", ip=ip)
    for release in db.execute(
        select(PaperRelease).where(
            PaperRelease.exam_id == exam.id,
            PaperRelease.status.notin_([ReleaseStatus.RELEASED, ReleaseStatus.EXPIRED]),
        )
    ).scalars():
        release.status = ReleaseStatus.EXPIRED
    exam.status = ExamStatus.CANCELLED
    record_audit(
        db,
        event="EXAM_CANCELLED",
        actor=actor,
        resource_type="internal_exam",
        resource_id=exam.id,
        ip=ip,
        metadata={"reason": reason},
    )
    db.flush()
    return exam


def _config_for(db: Session, exam_id: str) -> ExamConfiguration:
    config = db.execute(select(ExamConfiguration).where(ExamConfiguration.exam_id == exam_id)).scalar_one_or_none()
    if config is None:
        raise NotFound("Examination configuration")
    return config


def _exam_out(db: Session, actor: User, exam: InternalExam, *, include_config: bool = True) -> dict[str, Any]:
    subject = db.get(Subject, exam.subject_id)
    creator = db.get(User, exam.created_by)
    topics = db.execute(
        select(Topic.id, Topic.name, Topic.unit, ExamTopic.weight_percent)
        .join(ExamTopic, ExamTopic.topic_id == Topic.id)
        .where(ExamTopic.exam_id == exam.id)
    ).all()
    config = db.execute(select(ExamConfiguration).where(ExamConfiguration.exam_id == exam.id)).scalar_one_or_none()
    request = (
        db.execute(
            select(GenerationRequest)
            .where(GenerationRequest.exam_id == exam.id)
            .order_by(GenerationRequest.created_at.desc())
        )
        .scalars()
        .first()
    )
    paper = paper_service.latest_paper(db, exam.id)
    bundle = (
        db.execute(select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)).scalar_one_or_none()
        if paper
        else None
    )
    opens, closes = release_window(exam)
    result: dict[str, Any] = {
        "id": exam.id,
        "source": exam.source,
        "can_edit": can_edit_exam(actor, exam),
        "created_by_id": exam.created_by,
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
        "paper_version": paper.version if paper else None,
        "paper_file_name": bundle.original_name if bundle else "",
        "paper_page_count": bundle.page_count if bundle else 0,
        "replaceable_until": opens if exam.source == ExamSource.UPLOADED else None,
        "window_opens_at": opens,
        "window_closes_at": closes,
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
        assigned = teacher_subject_ids(db, actor.id)
        query = query.where(or_(InternalExam.created_by == actor.id, InternalExam.subject_id.in_(assigned)))
    elif actor.role != Role.HOD:
        raise Forbidden("Students do not have access to internal examinations.")
    exams = db.execute(query.order_by(InternalExam.exam_date.desc())).scalars().all()
    return [_exam_out(db, actor, exam) for exam in exams]


def get_exam(db: Session, actor: User, exam_id: str) -> dict[str, Any]:
    exam = ensure_view_exam(db, actor, exam_id)
    return _exam_out(db, actor, exam)


def generation_status(db: Session, actor: User, exam_id: str) -> dict[str, Any]:
    exam = ensure_view_exam(db, actor, exam_id)
    request = (
        db.execute(
            select(GenerationRequest)
            .where(GenerationRequest.exam_id == exam.id)
            .order_by(GenerationRequest.created_at.desc())
        )
        .scalars()
        .first()
    )
    if request is None:
        raise NotFound("Generation request")
    return generation_service.request_status(db, request)


def secure_paper_for_teacher(db: Session, actor: User, paper_id: str) -> dict[str, Any]:
    paper = db.get(QuestionPaper, paper_id)
    if paper is None:
        raise NotFound("Paper")
    ensure_view_exam(db, actor, paper.exam_id)
    return paper_service.paper_for_teacher(db, paper)


def exam_secure_paper(db: Session, actor: User, exam_id: str) -> dict[str, Any]:
    exam = ensure_view_exam(db, actor, exam_id)
    paper = paper_service.latest_paper(db, exam.id)
    if paper is None:
        raise NotFound("Secured paper")
    return paper_service.paper_for_teacher(db, paper)


def integrity_check(db: Session, hod: User, paper_id: str, ip: str) -> dict[str, Any]:
    """Decrypt in memory to prove the ciphertext is intact. Counts only, audited."""
    result = paper_service.verify_integrity(db, paper_id)
    record_audit(
        db,
        event="PAPER_INTEGRITY_VERIFIED" if result["integrity_verified"] else "PAPER_INTEGRITY_FAILED",
        actor=hod,
        resource_type="question_paper",
        resource_id=paper_id,
        success=bool(result["integrity_verified"]),
        ip=ip,
    )
    db.commit()
    return result


# --- HOD release workflow -------------------------------------------------
def release_window(exam: InternalExam) -> tuple[datetime, datetime]:
    exam_date = ensure_utc(exam.exam_date)
    opens = exam_date - timedelta(minutes=settings.PRINT_EARLY_OPEN_MINUTES)
    closes = exam_date + timedelta(minutes=settings.RELEASE_WINDOW_MINUTES)
    return opens, closes


def _prints_used(db: Session, paper_id: str) -> int:
    """Prints across every release of this paper version: re-authorising never resets the cap."""
    return int(
        db.execute(
            select(func.coalesce(func.sum(PaperRelease.print_count), 0)).where(PaperRelease.paper_id == paper_id)
        ).scalar_one()
    )


def _flag_hod_self_handling(db: Session, hod: User, exam: InternalExam, ip: str, action: str) -> None:
    """The HOD both prepared and releases this paper: no second pair of eyes."""
    if exam.created_by != hod.id:
        return
    record_security_event(
        db,
        event_type="HOD_OWN_PAPER",
        summary=f"HOD {action} a paper for an examination they created: {exam.title}",
        severity=Severity.WARNING,
        actor_id=hod.id,
        ip=ip,
        detail={"exam_id": exam.id, "action": action},
    )


def release_queue(db: Session, actor: User) -> list[dict[str, Any]]:
    if actor.role != Role.HOD:
        raise Forbidden("Only the HOD can view the release queue.")
    papers = db.execute(select(QuestionPaper).order_by(QuestionPaper.version.desc())).scalars().all()
    items: list[dict[str, Any]] = []
    now = _now()
    for paper in papers:
        if paper.state not in paper_service.SECURED_STATES | paper_service.RELEASE_STATES:
            continue
        exam = db.get(InternalExam, paper.exam_id)
        if exam is None or exam.status == ExamStatus.CANCELLED:
            continue
        opens, closes = release_window(exam)
        subject = db.get(Subject, exam.subject_id)
        bundle = db.execute(select(EncryptedPaper).where(EncryptedPaper.paper_id == paper.id)).scalar_one_or_none()
        release = (
            db.execute(
                select(PaperRelease)
                .where(PaperRelease.paper_id == paper.id, PaperRelease.status != ReleaseStatus.EXPIRED)
                .order_by(PaperRelease.authorized_at.desc())
            )
            .scalars()
            .first()
        )
        used = _prints_used(db, paper.id)
        authorizer = db.get(User, release.authorized_by) if release else None
        items.append(
            {
                "paper_id": paper.id,
                "exam_id": exam.id,
                "source": exam.source,
                "paper_version": paper.version,
                "page_count": bundle.page_count if bundle else 0,
                "prints_used": used,
                "prints_remaining": max(0, PRINT_LIMIT_PER_PAPER - used),
                "exam_title": exam.title,
                "subject_name": subject.name if subject else "",
                "exam_type": exam.exam_type,
                "exam_date": exam.exam_date,
                "duration_minutes": exam.duration_minutes,
                "total_marks": paper.total_marks or exam.total_marks,
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
                        "authorized_by_name": authorizer.full_name if authorizer else "",
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


def authorize_release(db: Session, hod: User, paper_id: str, reason: str, ip: str) -> dict[str, Any]:
    if hod.role != Role.HOD:
        raise Forbidden("Only the HOD can authorize paper release.")
    paper = db.get(QuestionPaper, paper_id)
    if paper is None:
        raise NotFound("Paper")
    exam = db.get(InternalExam, paper.exam_id)
    if exam is None:
        raise NotFound("Examination")
    if exam.status == ExamStatus.CANCELLED or PaperState(paper.state) == PaperState.DISCARDED:
        raise Conflict("This paper was cancelled or replaced and cannot be released.", "PAPER_DISCARDED")
    open_now, too_early, opens, closes = window_state(exam)
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
    if state in paper_service.SECURED_STATES:
        paper_service.transition(
            db, paper, PaperState.RELEASE_PENDING, actor=hod, reason=f"Release authorized: {reason}", ip=ip
        )
    elif state not in {PaperState.RELEASE_PENDING, PaperState.DECRYPTED, PaperState.PRINTED}:
        raise Conflict(
            f"Paper in state {paper.state} cannot be released. It must be encrypted and stored.",
            "INVALID_PAPER_STATE",
        )

    token, token_hash = generate_release_token()
    expires_at = _now() + timedelta(minutes=settings.RELEASE_TOKEN_TTL_MINUTES)
    for release in db.execute(
        select(PaperRelease).where(
            PaperRelease.paper_id == paper.id,
            PaperRelease.status.notin_([ReleaseStatus.RELEASED, ReleaseStatus.EXPIRED]),
        )
    ).scalars():
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
    _flag_hod_self_handling(db, hod, exam, ip, "authorized release of")
    db.commit()
    return {
        "paper_id": paper.id,
        "release_token": token,
        "expires_at": expires_at,
        "exam_date": exam.exam_date,
        "window_closes_at": closes,
        "message": "Release authorized. The token is valid for this release session and expires shortly.",
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
    open_now, _, _, _ = window_state(exam)
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
        raise Forbidden("The release window is closed for this examination.", code="RELEASE_WINDOW_CLOSED")
    release = (
        db.execute(
            select(PaperRelease)
            .where(PaperRelease.paper_id == paper.id, PaperRelease.status != ReleaseStatus.EXPIRED)
            .order_by(PaperRelease.authorized_at.desc())
        )
        .scalars()
        .first()
    )
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
        raise Forbidden("The release token expired. Authorize the release again.", code="RELEASE_TOKEN_EXPIRED")
    if not hod.mfa_enabled:
        raise Forbidden("MFA is required for this operation.", code="MFA_REQUIRED")
    return paper, exam, release, encrypted


def decrypt_for_release(db: Session, hod: User, paper_id: str, token: str, reason: str, ip: str) -> dict[str, Any]:
    paper, exam, release, encrypted = _validate_release(db, hod, paper_id, token, ip, "PAPER_DECRYPT")
    body = paper_service.decrypt_body(paper, encrypted)
    verified = paper.plaintext_sha256 == encrypted.plaintext_sha256 == sha256_hex(body)
    if not verified:
        raise Conflict("Plaintext hash mismatch: refusing to decrypt.", "INTEGRITY_FAILURE")
    summary = paper_service.describe_body(encrypted, body)
    del body
    if PaperState(paper.state) == PaperState.RELEASE_PENDING:
        paper_service.transition(
            db, paper, PaperState.DECRYPTED, actor=hod, reason=f"Authorized decryption: {reason}", ip=ip
        )
    release.status = ReleaseStatus.DECRYPTED if release.status == ReleaseStatus.AUTHORIZED else release.status
    release.decrypted_at = release.decrypted_at or _now()
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
            "question_count": summary["question_count"],
            "page_count": summary["page_count"],
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
        "question_count": summary["question_count"],
        "page_count": summary["page_count"],
        "content_type": encrypted.content_type,
        "sections": [
            {
                "label": section["label"],
                "questions": section["questions"],
                "marks_per_question": section["marks_per_question"],
            }
            for section in summary["sections"]
        ],
        "plaintext_ttl_seconds": 0,
        "message": (
            "Integrity verified and paper decrypted in memory only. Generate the printable copy "
            "immediately; no plaintext copy is stored."
        ),
    }


def printable_pdf(db: Session, hod: User, paper_id: str, token: str, ip: str) -> tuple[bytes, dict[str, Any]]:
    from app.services.print_service import render_paper_pdf, stamp_uploaded_pdf

    paper, exam, release, encrypted = _validate_release(db, hod, paper_id, token, ip, "PAPER_PRINT")
    if PaperState(paper.state) not in {PaperState.DECRYPTED, PaperState.PRINTED}:
        raise Conflict("Decrypt the paper before printing.", "PAPER_NOT_DECRYPTED")
    used = _prints_used(db, paper.id)
    if used >= PRINT_LIMIT_PER_PAPER:
        record_audit(
            db,
            event="PAPER_PRINT_LIMIT_REJECTED",
            actor=hod,
            resource_type="question_paper",
            resource_id=paper.id,
            success=False,
            ip=ip,
            metadata={"prints_used": used},
        )
        db.commit()
        raise Conflict(
            f"The maximum of {PRINT_LIMIT_PER_PAPER} controlled prints has been reached for this paper.",
            "PRINT_LIMIT_REACHED",
        )
    copy_number = used + 1
    body = paper_service.decrypt_body(paper, encrypted)
    label = f"Controlled copy {copy_number}/{PRINT_LIMIT_PER_PAPER} - paper {paper.id[:8]} v{paper.version}"
    if encrypted.content_type == paper_service.PDF_CONTENT_TYPE:
        pdf_bytes = stamp_uploaded_pdf(body, label)
    else:
        pdf_bytes = render_paper_pdf(json.loads(body))
    del body
    release.print_count += 1
    release.printed_at = _now()
    release.status = ReleaseStatus.PRINTED
    if PaperState(paper.state) == PaperState.DECRYPTED:
        paper_service.transition(
            db, paper, PaperState.PRINTED, actor=hod, reason=f"Controlled print #{copy_number}", ip=ip
        )
    record_audit(
        db,
        event="PAPER_PRINTED",
        actor=hod,
        resource_type="question_paper",
        resource_id=paper.id,
        ip=ip,
        metadata={"print_count": copy_number, "bytes": len(pdf_bytes), "exam_id": exam.id},
    )
    record_security_event(
        db,
        event_type="PAPER_PRINTED",
        summary=f"Controlled print #{copy_number} for {exam.title}",
        severity=Severity.INFO,
        actor_id=hod.id,
        ip=ip,
        detail={"paper_id": paper.id},
    )
    db.commit()
    return pdf_bytes, {
        "paper_id": paper.id,
        "printed": True,
        "print_count": copy_number,
        "prints_remaining": max(0, PRINT_LIMIT_PER_PAPER - copy_number),
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
    release = (
        db.execute(
            select(PaperRelease).where(PaperRelease.paper_id == paper.id).order_by(PaperRelease.authorized_at.desc())
        )
        .scalars()
        .first()
    )
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
        metadata={"exam_id": exam.id, "print_count": _prints_used(db, paper.id)},
    )
    db.commit()
    return {"paper_id": paper.id, "state": paper.state, "message": "Paper marked as released."}


def release_history(db: Session, actor: User) -> list[dict[str, Any]]:
    if actor.role != Role.HOD:
        raise Forbidden("Only the HOD can view release history.")
    releases = db.execute(select(PaperRelease).order_by(PaperRelease.authorized_at.desc()).limit(200)).scalars().all()
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
        paper = paper_service.latest_paper(db, exam.id)
        open_now, too_early, opens, closes = window_state(exam)
        rows.append(
            {
                "id": exam.id,
                "title": exam.title,
                "source": exam.source,
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
