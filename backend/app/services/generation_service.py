"""Internal paper generation pipeline.

Teacher requirements -> RAG context -> candidate generation -> 4-level question
validation with retries -> constraint assembly -> Level-5 paper validation ->
lock -> AES-256-GCM encryption -> secure storage -> sealed candidate pool.

The pipeline stores only metadata/status visible to teachers. Question text is
never returned by any teacher-facing endpoint.
"""
from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import PROMPT_VERSION
from app.ai.provider import build_provider
from app.core.config import settings
from app.db.session import session_scope
from app.models.enums import (
    ExamStatus,
    GenerationStatus,
    PaperState,
    QuestionType,
    ValidationStatus,
)
from app.models.tables import (
    ExamConfiguration,
    ExamTopic,
    GenerationRequest,
    InternalExam,
    QuestionMetadata,
    QuestionPaper,
    QuestionPool,
    Subject,
    Topic,
    ValidationResult,
)
from app.rag.pipeline import retrieve
from app.services import paper_service
from app.services.audit_service import record_audit
from app.services.paper_assembly import (
    assemble,
    build_slots,
    render_paper_payload,
    target_counts,
)
from app.validation.pipeline import (
    min_quality_score,
    normalize_for_duplicate,
    validate_candidate,
    validate_paper,
)

logger = logging.getLogger("praxis.generation")


class GenerationError(RuntimeError):
    pass


STAGE_PROGRESS = {
    "QUEUED": 5,
    "RETRIEVING": 15,
    "GENERATING": 40,
    "VALIDATING": 55,
    "ASSEMBLING": 70,
    "FINAL_VALIDATION": 82,
    "ENCRYPTING": 92,
    "COMPLETED": 100,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _demo_note(mode: str) -> str:
    return (
        "DEMO generation: template-based questions grounded in uploaded material. "
        "Do not use for a real examination."
        if mode == "DEMO"
        else "Questions were produced by a real model and passed automated validation."
    )


def _mark_stage(db: Session, request: GenerationRequest, stage: str) -> None:
    # Stage is derived from request/paper fields; stage stored in exam instructions? No.
    # We keep a lightweight JSON on metadata-less request: reuse `error` only for errors.
    db.flush()


def _variant(spec: dict[str, Any], variant: int) -> dict[str, Any]:
    """Deterministic prompt variant used for regeneration attempts."""
    if variant <= 1:
        return spec
    styles = [
        "Use a scenario-based framing.",
        "Ask for comparison and justification.",
        "Ask the student to design and evaluate.",
        "Frame the question around a failure case to diagnose.",
    ]
    adjusted = dict(spec)
    adjusted["variation_hint"] = styles[(variant - 1) % len(styles)]
    adjusted["slot_index"] = spec["slot_index"] * 10 + variant
    return adjusted


def _spread(targets: dict[str, int]) -> list:
    """Round-robin a multi-set so adjacent slots vary rather than clumping.

    Grouping equal values (the original approach) gave every 10-mark slot the
    same topic and difficulty, which made balanced papers impossible to
    assemble for multi-mark patterns.
    """
    buckets = [[key] * max(0, int(count)) for key, count in sorted(targets.items())]
    result: list = []
    while True:
        progressed = False
        for bucket in buckets:
            if bucket:
                result.append(bucket.pop(0))
                progressed = True
        if not progressed:
            break
    return result


def _build_demand(
    total_slots: int,
    topic_targets: dict[str, int],
    topic_names: dict[str, str],
    difficulty_targets: dict[str, int],
    bloom_targets: dict[str, int],
) -> list[dict[str, str]]:
    difficulty_cycle = _spread(difficulty_targets)
    bloom_cycle = _spread(bloom_targets)
    if not difficulty_cycle:
        difficulty_cycle = ["MEDIUM"]
    if not bloom_cycle:
        bloom_cycle = ["UNDERSTAND"]

    demand: list[dict[str, str]] = []
    topic_pairs = [(topic_id, topic_names.get(topic_id, topic_id)) for topic_id in topic_targets]
    topic_cycle = _spread({topic_id: count for topic_id, count in topic_targets.items()})
    if not topic_cycle:
        topic_cycle = [next(iter(topic_names), "")]
    names_by_id = dict(topic_pairs)
    for index in range(total_slots):
        topic_id = topic_cycle[index % len(topic_cycle)]
        demand.append(
            {
                "topic_id": topic_id,
                "topic_name": names_by_id.get(topic_id, topic_names.get(topic_id, topic_id)),
                "difficulty": difficulty_cycle[index % len(difficulty_cycle)],
                "bloom_level": bloom_cycle[index % len(bloom_cycle)],
            }
        )
    return demand


def _generate_candidate(
    db: Session,
    *,
    exam: InternalExam,
    config: ExamConfiguration,
    request: GenerationRequest,
    provider,
    slot: dict[str, Any],
    demand: dict[str, str],
    context: list[dict[str, Any]],
    context_text: str,
    existing_embeddings: list[list[float]],
    existing_normalized: set[str],
    attempt: int,
) -> QuestionPool | None:
    spec = {
        "subject_name": exam.subject.name,
        "topic_name": demand["topic_name"],
        "unit": "",
        "marks": slot["marks"],
        "difficulty": demand["difficulty"],
        "bloom_level": demand["bloom_level"],
        "question_type": slot["question_type"],
        "slot_index": slot["slot_index"],
        "variation_hint": "",
    }
    spec = _variant(spec, attempt)
    try:
        candidates = provider.generate_questions(spec=spec, context=context)
    except Exception as exc:  # noqa: BLE001
        record_audit(
            db,
            event="QUESTION_GENERATION_ERROR",
            actor_role="SYSTEM",
            resource_type="generation_request",
            resource_id=request.id,
            success=False,
            metadata={"attempt": attempt, "error_type": type(exc).__name__},
        )
        return None
    if not candidates:
        return None
    candidate = candidates[0]
    candidate = {
        "question_text": str(candidate.get("question_text", "")).strip(),
        "question_type": candidate.get("question_type") or slot["question_type"],
        "marks": candidate.get("marks", slot["marks"]),
        "difficulty": candidate.get("difficulty", demand["difficulty"]),
        "bloom_level": candidate.get("bloom_level", demand["bloom_level"]),
        "topic": demand["topic_name"],
        "expected_answer": candidate.get("expected_answer", ""),
        "source_ref": candidate.get("source_ref", ""),
    }
    report = validate_candidate(
        question=candidate,
        spec={**spec, "topic_name": demand["topic_name"]},
        context_text=context_text,
        provider=provider,
        existing_embeddings=existing_embeddings,
        existing_normalized=existing_normalized,
    )
    request.candidates_generated += 1
    if not report.passed or report.quality_score < min_quality_score():
        request.candidates_rejected += 1
        record_audit(
            db,
            event="QUESTION_VALIDATION_REJECTED",
            actor_role="SYSTEM",
            resource_type="generation_request",
            resource_id=request.id,
            success=False,
            metadata={
                "attempt": attempt,
                "quality": report.quality_score,
                "issue_codes": report.issues[:8],
            },
        )
        return None

    question = QuestionPool(
        exam_id=exam.id,
        generation_request_id=request.id,
        subject_id=exam.subject_id,
        topic_id=demand["topic_id"],
        unit=next((topic.unit for topic in exam.subject.topics if topic.id == demand["topic_id"]), ""),
        question_text=candidate["question_text"],
        question_type=str(candidate["question_type"]),
        marks=int(candidate["marks"]),
        difficulty=str(candidate["difficulty"]),
        bloom_level=str(candidate["bloom_level"]),
        expected_answer=candidate["expected_answer"],
        source_ref=str(candidate["source_ref"])[:255],
        quality_score=report.quality_score,
        validation_status=ValidationStatus.PASSED,
        generation_attempt=attempt,
        embedding=report.embedding,
        created_at=_now(),
    )
    db.add(question)
    db.flush()
    db.add(
        QuestionMetadata(
            question_id=question.id,
            generation_model=provider.model,
            prompt_version=PROMPT_VERSION,
            references=[
                {
                    "document_name": chunk["document_name"],
                    "document_id": chunk["document_id"],
                    "chunk_id": chunk["chunk_id"],
                    "score": chunk["score"],
                }
                for chunk in context[:4]
            ],
            raw_response={"mode": provider.mode, "mode_note": _demo_note(provider.mode)},
        )
    )
    for level in report.levels:
        db.add(
            ValidationResult(
                question_id=question.id,
                exam_id=exam.id,
                level=level.level,
                validator=level.validator,
                passed=level.passed,
                score=round(level.score, 3),
                issues=[issue.as_dict() for issue in level.issues],
            )
        )
    # A validation row that carries no question text is safe to show to teachers.
    existing_embeddings.append(report.embedding)
    existing_normalized.add(normalize_duplicate(candidate["question_text"]))
    return question


def normalize_duplicate(text: str) -> str:
    return normalize_for_duplicate(text)


def _pool_export(db: Session, exam_id: str) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(QuestionPool).where(
                QuestionPool.exam_id == exam_id,
                QuestionPool.validation_status == ValidationStatus.PASSED,
                QuestionPool.question_text.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "question_text": row.question_text,
            "question_type": row.question_type,
            "marks": row.marks,
            "difficulty": row.difficulty,
            "bloom_level": row.bloom_level,
            "topic_id": row.topic_id,
            "topic_name": row.topic.name if row.topic else "",
            "quality_score": row.quality_score,
            "expected_answer": row.expected_answer,
            "validation_status": "PASSED",
        }
        for row in rows
    ]


def run_generation(request_id: str) -> None:
    """Execute one generation request end-to-end. Safe to run in a worker."""
    with session_scope() as db:
        request = db.get(GenerationRequest, request_id)
        if request is None:
            logger.warning("generation request missing", extra={"event": "generation_missing"})
            return
        if request.status not in {GenerationStatus.QUEUED, GenerationStatus.FAILED}:
            return
        exam = db.get(InternalExam, request.exam_id)
        if exam is None:
            request.status = GenerationStatus.FAILED
            request.error = "Examination no longer exists"
            return
        config = db.execute(
            select(ExamConfiguration).where(ExamConfiguration.exam_id == exam.id)
        ).scalar_one_or_none()
        if config is None or not config.frozen:
            request.status = GenerationStatus.FAILED
            request.error = "Exam configuration is missing or not frozen"
            db.flush()
            return

        request.status = GenerationStatus.RUNNING
        request.started_at = _now()
        request.provider = build_provider().name
        request.mode = build_provider().mode
        exam.status = ExamStatus.PROCESSING
        db.flush()

        record_audit(
            db,
            event="PAPER_GENERATION_REQUESTED",
            actor_role="SYSTEM",
            resource_type="internal_exam",
            resource_id=exam.id,
            metadata={
                "request_id": request.id,
                "provider": request.provider,
                "mode": request.mode,
                "retry_limit": config.retry_limit,
            },
        )

        paper = (
            db.execute(
                select(QuestionPaper).where(
                    QuestionPaper.exam_id == exam.id, QuestionPaper.version == 1
                )
            ).scalar_one_or_none()
        )
        if paper is None:
            paper = paper_service.create_paper(
                db, exam_id=exam.id, generation_request_id=request.id, version=1
            )
        if PaperState(paper.state) == PaperState.SUBMITTED:
            paper_service.transition(
                db, paper, PaperState.GENERATING, actor_role="SYSTEM", reason="Generation started"
            )
        request.paper_id = paper.id  # convenience for API layer (set on request object)

        provider = build_provider()
        subject = db.get(Subject, exam.subject_id)
        topic_rows = (
            db.execute(
                select(Topic, ExamTopic.weight_percent)
                .join(ExamTopic, ExamTopic.topic_id == Topic.id)
                .where(ExamTopic.exam_id == exam.id)
            )
            .all()
        )
        if not topic_rows:
            raise GenerationError("No examination topics configured")
        topic_names = {topic.id: topic.name for topic, _ in topic_rows}
        topic_weights = {topic.id: weight for topic, weight in topic_rows}

        pattern = config.pattern
        slots = build_slots(pattern)
        total_slots = len(slots)
        topic_targets = target_counts(topic_weights, total_slots)
        difficulty_targets = target_counts(config.difficulty_distribution, total_slots)
        bloom_targets = target_counts(config.bloom_distribution, total_slots)
        demand = _build_demand(total_slots, topic_targets, topic_names, difficulty_targets, bloom_targets)

        existing = (
            db.execute(select(QuestionPool).where(QuestionPool.exam_id == exam.id))
            .scalars()
            .all()
        )
        existing_embeddings = [row.embedding for row in existing if row.embedding]
        existing_normalized = {
            normalize_for_duplicate(row.question_text or "") for row in existing
        }

        # Retrieval context per topic so questions stay grounded.
        contexts: dict[str, tuple[list[dict], str]] = {}
        for topic_id, topic_name in topic_names.items():
            chunks = retrieve(
                db,
                subject_id=exam.subject_id,
                query=f"{topic_name} {subject.name if subject else ''}",
                top_k=5,
            )
            contexts[topic_id] = (
                chunks,
                "\n\n".join(chunk["content"] for chunk in chunks)[:6000],
            )

        generated = 0
        for slot, demand_item in zip(slots, demand):
            context, context_text = contexts.get(demand_item["topic_id"], ([], ""))
            created = None
            for attempt in range(1, config.retry_limit + 1):
                request.attempts += 1
                created = _generate_candidate(
                    db,
                    exam=exam,
                    config=config,
                    request=request,
                    provider=provider,
                    slot=slot,
                    demand=demand_item,
                    context=context,
                    context_text=context_text,
                    existing_embeddings=existing_embeddings,
                    existing_normalized=existing_normalized,
                    attempt=attempt,
                )
                if created is not None:
                    break
            if created is None:
                record_audit(
                    db,
                    event="QUESTION_SLOT_EXHAUSTED",
                    actor_role="SYSTEM",
                    resource_type="internal_exam",
                    resource_id=exam.id,
                    success=False,
                    metadata={"marks": slot["marks"], "topic": demand_item["topic_name"]},
                )
                request.status = GenerationStatus.FAILED
                request.error = (
                    "No candidate passed validation within the retry limit for one question slot. "
                    "Nothing was included in the paper. Refine topics/marks or upload richer syllabus material."
                )
                request.completed_at = _now()
                exam.status = ExamStatus.FAILED
                db.flush()
                return
            generated += 1

        # Extra candidates give the assembler room to satisfy distributions.
        extra_needed = max(2, total_slots // 3)
        extras: list[dict[str, Any]] = []
        for index in range(extra_needed):
            base = demand[index % len(demand)]
            slot = slots[index % len(slots)]
            context, context_text = contexts.get(base["topic_id"], ([], ""))
            for attempt in range(1, config.retry_limit + 1):
                request.attempts += 1
                extra = _generate_candidate(
                    db,
                    exam=exam,
                    config=config,
                    request=request,
                    provider=provider,
                    slot=slot,
                    demand=base,
                    context=context,
                    context_text=context_text,
                    existing_embeddings=existing_embeddings,
                    existing_normalized=existing_normalized,
                    attempt=attempt + 3,
                )
                if extra is not None:
                    extras.append({"id": extra.id})
                    break

        paper_service.transition(
            db, paper, PaperState.VALIDATING, actor_role="SYSTEM", reason="Candidate pool validated"
        )

        assembled = None
        final_validation: dict[str, Any] | None = None
        for assembly_round in range(3):
            pool = _pool_export(db, exam.id)
            assembled = assemble(
                pool,
                pattern=[item.model_dump() if hasattr(item, "model_dump") else item for item in pattern],
                topic_distribution=config.topic_distribution,
                difficulty_distribution=config.difficulty_distribution,
                bloom_distribution=config.bloom_distribution,
                topic_names={
                    topic.id: topic.name for topic, _ in topic_rows
                },
            )
            if assembled is None:
                # Add more candidates for the next round.
                for index in range(3):
                    base = demand[(assembly_round + index) % len(demand)]
                    slot = slots[(assembly_round + index) % len(slots)]
                    context, context_text = contexts.get(base["topic_id"], ([], ""))
                    for attempt in range(1, config.retry_limit + 1):
                        request.attempts += 1
                        extra = _generate_candidate(
                            db,
                            exam=exam,
                            config=config,
                            request=request,
                            provider=provider,
                            slot=slot,
                            demand=base,
                            context=context,
                            context_text=context_text,
                            existing_embeddings=existing_embeddings,
                            existing_normalized=existing_normalized,
                            attempt=attempt + 5 + assembly_round,
                        )
                        if extra is not None:
                            break
                continue
            exam_meta = {
                "title": exam.title,
                "subject": subject.name if subject else "",
                "subject_code": subject.code if subject else "",
                "exam_type": exam.exam_type,
                "exam_date": exam.exam_date.isoformat(),
                "duration_minutes": exam.duration_minutes,
                "venue": exam.venue,
                "instructions": exam.instructions or config.instructions,
                "total_marks": exam.total_marks,
                "paper_version": 1,
            }
            payload = render_paper_payload(
                exam=exam_meta,
                sections=assembled["sections"],
                questions=assembled["questions"],
            )
            final_validation = validate_paper(
                payload,
                {
                    "total_marks": exam.total_marks,
                    "pattern": [
                        item.model_dump() if hasattr(item, "model_dump") else item
                        for item in config.pattern
                    ],
                    "difficulty_distribution": config.difficulty_distribution,
                    "bloom_distribution": config.bloom_distribution,
                    "topic_distribution": config.topic_distribution,
                    "topic_names": {topic.id: topic.name for topic, _ in topic_rows},
                },
            )
            if final_validation["passed"]:
                break
            record_audit(
                db,
                event="PAPER_FINAL_VALIDATION_FAILED",
                actor_role="SYSTEM",
                resource_type="question_paper",
                resource_id=paper.id,
                success=False,
                metadata={
                    "round": assembly_round + 1,
                    "issue_codes": [issue["code"] for issue in final_validation["issues"]][:8],
                },
            )
            assembled = None

        if assembled is None or final_validation is None or not final_validation["passed"]:
            request.status = GenerationStatus.FAILED
            request.error = (
                "Paper assembly could not satisfy the configured marks/topic/difficulty/Bloom "
                "constraints. No paper was created. Adjust the configuration and regenerate."
            )
            request.completed_at = _now()
            exam.status = ExamStatus.FAILED
            db.add(
                ValidationResult(
                    exam_id=exam.id,
                    paper_id=paper.id,
                    level=5,
                    validator="paper",
                    passed=False,
                    score=final_validation["score"] if final_validation else 0,
                    issues=(final_validation or {}).get("issues", [])[:20],
                )
            )
            db.flush()
            return

        paper.structure = {
            "sections": assembled["sections"],
            "metrics": assembled["metrics"],
            "question_ids": [question["id"] for question in assembled["questions"]],
        }
        paper.total_marks = assembled["metrics"]["total_marks"]
        paper.question_count = assembled["metrics"]["question_count"]
        paper.validation_summary = {
            "passed": True,
            "score": final_validation["score"],
            "metrics": final_validation["metrics"],
            "issue_count": len(final_validation["issues"]),
            "validated_at": _now().isoformat(),
        }
        db.execute(
            QuestionPool.__table__.update()
            .where(QuestionPool.id.in_([q["id"] for q in assembled["questions"]]))
            .values(selected=True)
        )
        db.add(
            ValidationResult(
                exam_id=exam.id,
                paper_id=paper.id,
                level=5,
                validator="paper",
                passed=True,
                score=final_validation["score"],
                issues=final_validation["issues"][:20],
            )
        )
        paper_service.transition(
            db, paper, PaperState.ASSEMBLED, actor_role="SYSTEM", reason="Constraint assembly complete"
        )
        paper_service.transition(
            db,
            paper,
            PaperState.VALIDATED,
            actor_role="SYSTEM",
            reason=f"Level-5 validation passed (score {final_validation['score']})",
        )
        paper_service.lock_paper(db, paper, None)
        paper_service.encrypt_and_store(db, paper, payload, actor=None, seal=True)
        paper_service.mark_exam_secured(db, exam.id)

        request.status = GenerationStatus.COMPLETED
        request.completed_at = _now()
        request.error = None
        db.flush()
        logger.info(
            "paper generation completed",
            extra={
                "event": "generation_completed",
                "request_id": request.id,
                "questions": paper.question_count,
            },
        )


def request_status(db: Session, request: GenerationRequest) -> dict[str, Any]:
    paper = None
    if getattr(request, "paper_id", None):
        paper = db.get(QuestionPaper, request.paper_id)
    if paper is None:
        paper = (
            db.execute(
                select(QuestionPaper)
                .where(QuestionPaper.exam_id == request.exam_id)
                .order_by(QuestionPaper.version.desc())
            )
            .scalars()
            .first()
        )
    validations = (
        db.execute(
            select(ValidationResult)
            .where(ValidationResult.exam_id == request.exam_id)
            .order_by(ValidationResult.created_at.desc())
            .limit(40)
        )
        .scalars()
        .all()
    )
    level_summary: dict[str, dict[str, int]] = {}
    for row in validations:
        level_key = "paper" if row.level == 5 else f"level_{row.level}"
        bucket = level_summary.setdefault(level_key, {"passed": 0, "failed": 0})
        bucket["passed" if row.passed else "failed"] += 1
    stage = "COMPLETED" if request.status == GenerationStatus.COMPLETED else (
        "FAILED" if request.status == GenerationStatus.FAILED else (
            "GENERATING" if request.status == GenerationStatus.RUNNING else "QUEUED"
        )
    )
    if paper and paper.state in {PaperState.VALIDATING, PaperState.ASSEMBLED, PaperState.VALIDATED}:
        stage = "FINAL_VALIDATION"
    if paper and paper.state in {PaperState.ENCRYPTED, PaperState.STORED}:
        stage = "COMPLETED" if request.status == GenerationStatus.COMPLETED else "ENCRYPTING"
    progress = STAGE_PROGRESS.get(stage, 10)
    if request.status == GenerationStatus.COMPLETED:
        progress = 100
    secure_summary = "Paper not yet secured"
    if paper and paper.state in {PaperState.ENCRYPTED, PaperState.STORED, PaperState.RELEASE_PENDING,
                                 PaperState.DECRYPTED, PaperState.PRINTED, PaperState.RELEASED}:
        secure_summary = (
            "Generation completed. Validation passed. Paper encrypted with AES-256-GCM and stored."
        )
    elif request.status == GenerationStatus.FAILED:
        secure_summary = "Generation failed - no paper was secured."
    return {
        "request_id": request.id,
        "exam_id": request.exam_id,
        "status": request.status,
        "mode": request.mode,
        "provider": request.provider,
        "attempts": request.attempts,
        "candidates_generated": request.candidates_generated,
        "candidates_rejected": request.candidates_rejected,
        "error": request.error,
        "stage": stage,
        "progress": progress,
        "paper_state": paper.state if paper else None,
        "paper_id": paper.id if paper else None,
        "validation": level_summary,
        "started_at": request.started_at,
        "completed_at": request.completed_at,
        "secure_summary": secure_summary,
    }
