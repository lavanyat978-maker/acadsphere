"""Online practice module (fully independent from the secure internal exam workflow).

Access rules live in `app.services.access`:
* students see published tests only in subjects they are enrolled in;
* teachers see every test in their assigned subjects but change only their own;
* the HOD sees and changes everything.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.provider import build_provider
from app.analytics.engine import attempt_history, teacher_test_analytics
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.timeutils import ensure_utc
from app.models.enums import AttemptStatus, BloomLevel, Difficulty, PracticeTestStatus, Role
from app.models.tables import (
    PracticeAnswer,
    PracticeAttempt,
    PracticeQuestion,
    PracticeResult,
    PracticeTest,
    PracticeTestTopic,
    Subject,
    Topic,
    User,
)
from app.rag.pipeline import retrieve
from app.schemas.practice import (
    GeneratePracticeQuestionsRequest,
    PracticeQuestionCreate,
    PracticeTestCreate,
    PracticeTestUpdate,
)
from app.services.access import (
    can_edit_test,
    ensure_edit_test,
    ensure_subject_staff,
    ensure_view_test,
    student_subject_ids,
    teacher_subject_ids,
)

# Submissions this long after the deadline are still accepted as on-time, to
# absorb network latency. Anything later is graded from the autosave instead.
GRACE_SECONDS = 30

# Allowed status changes. Moving back to DRAFT is only possible while nobody has
# attempted the test, otherwise edits would silently rewrite past results.
STATUS_TRANSITIONS: dict[str, set[str]] = {
    PracticeTestStatus.DRAFT: {PracticeTestStatus.PUBLISHED, PracticeTestStatus.ARCHIVED},
    PracticeTestStatus.PUBLISHED: {PracticeTestStatus.ARCHIVED, PracticeTestStatus.DRAFT},
    PracticeTestStatus.ARCHIVED: {PracticeTestStatus.PUBLISHED, PracticeTestStatus.DRAFT},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _subject_name(db: Session, subject_id: str) -> str:
    subject = db.get(Subject, subject_id)
    return subject.name if subject else ""


def _has_attempts(db: Session, test_id: str) -> bool:
    return db.execute(select(PracticeAttempt.id).where(PracticeAttempt.test_id == test_id).limit(1)).first() is not None


def availability(test: PracticeTest, now: datetime | None = None) -> str:
    now = now or _now()
    if test.opens_at and ensure_utc(test.opens_at) > now:
        return "UPCOMING"
    if test.closes_at and ensure_utc(test.closes_at) <= now:
        return "CLOSED"
    return "OPEN"


def _test_out(
    db: Session,
    test: PracticeTest,
    *,
    viewer: User | None = None,
) -> dict[str, Any]:
    student_id = viewer.id if viewer is not None and viewer.role == Role.STUDENT else None
    attempts = db.execute(select(PracticeAttempt).where(PracticeAttempt.test_id == test.id)).scalars().all()
    own = [attempt for attempt in attempts if attempt.student_id == student_id] if student_id else []
    best = None
    last_at = None
    if student_id:
        results = db.execute(
            select(PracticeResult).where(
                PracticeResult.test_id == test.id, PracticeResult.student_id == student_id
            )
        ).scalars().all()
        if results:
            best = round(max(result.percentage for result in results), 2)
            last_at = max(result.created_at for result in results)
    creator = db.get(User, test.created_by) if viewer is not None and viewer.role != Role.STUDENT else None
    return {
        "id": test.id,
        "subject_id": test.subject_id,
        "subject_name": _subject_name(db, test.subject_id),
        "title": test.title,
        "description": test.description,
        "duration_minutes": test.duration_minutes,
        "difficulty": test.difficulty,
        "question_count": test.question_count,
        "total_marks": test.total_marks,
        "status": test.status,
        "topics": [link.topic.name for link in test.topics if link.topic],
        "attempt_count": len(own) if student_id else len(attempts),
        "best_score": best,
        "last_attempt_at": last_at,
        "can_edit": bool(viewer is not None and can_edit_test(viewer, test)),
        "created_by_name": creator.full_name if creator else "",
        "opens_at": test.opens_at,
        "closes_at": test.closes_at,
        "max_attempts": test.max_attempts,
        "availability": availability(test),
        "attempts_remaining": (
            max(0, test.max_attempts - len(own)) if student_id and test.max_attempts else None
        ),
    }


def _question_out(question: PracticeQuestion, *, include_answers: bool) -> dict[str, Any]:
    return {
        "id": question.id,
        "question_text": question.question_text,
        "question_type": question.question_type,
        "options": question.options or [],
        "marks": question.marks,
        "difficulty": question.difficulty,
        "bloom_level": question.bloom_level,
        "topic_id": question.topic_id,
        "topic_name": question.topic.name if question.topic else "",
        "position": question.position,
        "explanation": question.explanation if include_answers else None,
        "correct_answers": list(question.correct_answers or []) if include_answers else None,
    }


def test_detail(db: Session, test: PracticeTest, *, viewer: User | None = None, include_answers: bool = True) -> dict[str, Any]:
    base = _test_out(db, test, viewer=viewer)
    base["questions"] = [
        _question_out(question, include_answers=include_answers)
        for question in sorted(test.questions, key=lambda item: item.position)
    ]
    return base


# --- Authoring ------------------------------------------------------------
def create_test(db: Session, teacher: User, payload: PracticeTestCreate) -> dict[str, Any]:
    subject = ensure_subject_staff(db, teacher, payload.subject_id)
    topic_rows = db.execute(
        select(Topic).where(Topic.subject_id == subject.id, Topic.id.in_(payload.topic_ids))
    ).scalars().all()
    if len(topic_rows) != len(set(payload.topic_ids)):
        raise Conflict("One or more topics do not belong to the selected subject.", "INVALID_TOPICS")
    test = PracticeTest(
        subject_id=subject.id,
        created_by=teacher.id,
        title=payload.title,
        description=payload.description,
        duration_minutes=payload.duration_minutes,
        difficulty=payload.difficulty,
        status=PracticeTestStatus.DRAFT,
        opens_at=payload.opens_at,
        closes_at=payload.closes_at,
        max_attempts=payload.max_attempts,
    )
    db.add(test)
    db.flush()
    for topic_id in dict.fromkeys(payload.topic_ids):
        db.add(PracticeTestTopic(test_id=test.id, topic_id=topic_id))
    db.flush()
    for index, question in enumerate(payload.questions):
        _add_question(db, test, question, index)
    _sync_counts(db, test)
    db.flush()
    return {"test_id": test.id, "detail": test_detail(db, test, viewer=teacher)}


def _add_question(db: Session, test: PracticeTest, payload: PracticeQuestionCreate, index: int) -> PracticeQuestion:
    if payload.topic_id not in {link.topic_id for link in test.topics}:
        raise Conflict("Question topic must be one of the test topics.", "INVALID_QUESTION_TOPIC")
    question = PracticeQuestion(
        test_id=test.id,
        topic_id=payload.topic_id,
        question_text=payload.question_text,
        question_type=payload.question_type,
        options=[option.model_dump() for option in payload.options],
        correct_answers=list(payload.correct_answers),
        explanation=payload.explanation,
        marks=payload.marks,
        difficulty=payload.difficulty,
        bloom_level=payload.bloom_level,
        position=index,
    )
    db.add(question)
    db.flush()
    return question


def _sync_counts(db: Session, test: PracticeTest) -> None:
    db.flush()
    questions = db.execute(select(PracticeQuestion).where(PracticeQuestion.test_id == test.id)).scalars().all()
    test.question_count = len(questions)
    test.total_marks = sum(question.marks for question in questions)


def list_tests(db: Session, user: User, subject_id: str | None = None) -> list[dict[str, Any]]:
    query = select(PracticeTest)
    if user.role == Role.TEACHER:
        assigned = teacher_subject_ids(db, user.id)
        query = query.where(or_(PracticeTest.created_by == user.id, PracticeTest.subject_id.in_(assigned)))
    elif user.role == Role.STUDENT:
        enrolled = student_subject_ids(db, user.id)
        if not enrolled:
            return []
        query = query.where(
            PracticeTest.status == PracticeTestStatus.PUBLISHED, PracticeTest.subject_id.in_(enrolled)
        )
    if subject_id:
        query = query.where(PracticeTest.subject_id == subject_id)
    tests = db.execute(query.order_by(PracticeTest.created_at.desc())).scalars().all()
    return [_test_out(db, test, viewer=user) for test in tests]


def _student_test(db: Session, student: User, test_id: str) -> PracticeTest:
    test = db.get(PracticeTest, test_id)
    if test is None:
        raise NotFound("Practice test")
    if test.status != PracticeTestStatus.PUBLISHED:
        raise Forbidden("This practice test is not published.", code="TEST_NOT_PUBLISHED")
    if test.subject_id not in student_subject_ids(db, student.id):
        raise Forbidden("You are not enrolled in this subject.", code="NOT_ENROLLED")
    return test


def get_test(db: Session, user: User, test_id: str, *, include_answers: bool = False) -> dict[str, Any]:
    if user.role == Role.STUDENT:
        test = _student_test(db, user, test_id)
        include_answers = False
    else:
        test = ensure_view_test(db, user, test_id)
    return test_detail(db, test, viewer=user, include_answers=include_answers)


def _ensure_editable_content(db: Session, test: PracticeTest) -> None:
    if test.status != PracticeTestStatus.DRAFT:
        raise Conflict("Questions can only be changed while the test is a draft.", "TEST_NOT_DRAFT")


def update_test(db: Session, user: User, test_id: str, payload: PracticeTestUpdate) -> dict[str, Any]:
    test = ensure_edit_test(db, user, test_id)
    changes = payload.model_dump(exclude_unset=True)
    content_change = any(changes.get(field) is not None for field in ("title", "description", "duration_minutes"))
    # Once students have attempted a published test its content is fixed, but the
    # schedule (open/close dates, attempt limit) can still be adjusted.
    if content_change and test.status == PracticeTestStatus.PUBLISHED and _has_attempts(db, test.id):
        raise Conflict(
            "A published test with attempts cannot be edited. Archive it and create a new test. "
            "The availability window and attempt limit can still be changed.",
            "TEST_LOCKED",
        )
    for field in ("title", "description", "duration_minutes"):
        if changes.get(field) is not None:
            setattr(test, field, changes[field])
    # Schedule fields may be cleared with an explicit null.
    for field in ("opens_at", "closes_at", "max_attempts"):
        if field in changes:
            setattr(test, field, changes[field])
    if test.opens_at and test.closes_at and ensure_utc(test.closes_at) <= ensure_utc(test.opens_at):
        raise Conflict("The closing time must be after the opening time.", "INVALID_WINDOW")
    db.flush()
    return test_detail(db, test, viewer=user)


def set_status(db: Session, user: User, test_id: str, status: str) -> dict[str, Any]:
    test = ensure_edit_test(db, user, test_id)
    current = PracticeTestStatus(test.status)
    target = PracticeTestStatus(status)
    if target == current:
        return test_detail(db, test, viewer=user)
    if target not in STATUS_TRANSITIONS[current]:
        raise Conflict(f"A {current.lower()} test cannot move to {target.lower()}.", "INVALID_TEST_STATUS")
    if target == PracticeTestStatus.DRAFT and _has_attempts(db, test.id):
        raise Conflict(
            "Students have already attempted this test, so it cannot return to draft. "
            "Archive it and create a new version instead.",
            "TEST_HAS_ATTEMPTS",
        )
    if target == PracticeTestStatus.PUBLISHED:
        _sync_counts(db, test)
        if test.question_count == 0:
            raise Conflict("Add at least one question before publishing.", "EMPTY_TEST")
        test.published_at = _now()
    test.status = target
    db.flush()
    return test_detail(db, test, viewer=user)


def add_questions(db: Session, user: User, test_id: str, payloads: list[PracticeQuestionCreate]) -> dict[str, Any]:
    test = ensure_edit_test(db, user, test_id)
    _ensure_editable_content(db, test)
    existing = db.execute(select(PracticeQuestion).where(PracticeQuestion.test_id == test.id)).scalars().all()
    offset = len(existing)
    seen = {question.question_text.strip().lower() for question in existing}
    added = 0
    for payload in payloads:
        if payload.question_text.strip().lower() in seen:
            continue
        _add_question(db, test, payload, offset + added)
        seen.add(payload.question_text.strip().lower())
        added += 1
    _sync_counts(db, test)
    db.flush()
    db.refresh(test)
    return test_detail(db, test, viewer=user)


def _question_for(db: Session, test: PracticeTest, question_id: str) -> PracticeQuestion:
    question = db.get(PracticeQuestion, question_id)
    if question is None or question.test_id != test.id:
        raise NotFound("Question")
    return question


def update_question(
    db: Session, user: User, test_id: str, question_id: str, payload: PracticeQuestionCreate
) -> dict[str, Any]:
    test = ensure_edit_test(db, user, test_id)
    _ensure_editable_content(db, test)
    question = _question_for(db, test, question_id)
    if payload.topic_id not in {link.topic_id for link in test.topics}:
        raise Conflict("Question topic must be one of the test topics.", "INVALID_QUESTION_TOPIC")
    question.topic_id = payload.topic_id
    question.question_text = payload.question_text
    question.question_type = payload.question_type
    question.options = [option.model_dump() for option in payload.options]
    question.correct_answers = list(payload.correct_answers)
    question.explanation = payload.explanation
    question.marks = payload.marks
    question.difficulty = payload.difficulty
    question.bloom_level = payload.bloom_level
    _sync_counts(db, test)
    db.flush()
    return test_detail(db, test, viewer=user)


def delete_question(db: Session, user: User, test_id: str, question_id: str) -> dict[str, Any]:
    test = ensure_edit_test(db, user, test_id)
    _ensure_editable_content(db, test)
    question = _question_for(db, test, question_id)
    db.delete(question)
    db.flush()
    remaining = db.execute(
        select(PracticeQuestion).where(PracticeQuestion.test_id == test.id).order_by(PracticeQuestion.position)
    ).scalars().all()
    for index, item in enumerate(remaining):
        item.position = index
    _sync_counts(db, test)
    db.flush()
    db.refresh(test)
    return test_detail(db, test, viewer=user)


def _enum_or(value: Any, enum: type, default: str) -> str:
    text = str(value or "").strip().upper()
    return text if text in {member.value for member in enum} else default


def generate_questions(
    db: Session, user: User, test_id: str, payload: GeneratePracticeQuestionsRequest
) -> dict[str, Any]:
    test = ensure_edit_test(db, user, test_id)
    _ensure_editable_content(db, test)
    topic_links = {link.topic_id: link.topic for link in test.topics if link.topic}
    if not topic_links:
        raise Conflict("Select at least one topic first.", "NO_TOPICS")
    if payload.topic_id and payload.topic_id not in topic_links:
        raise Conflict("That topic is not part of this test.", "INVALID_TOPIC")
    topics = [topic_links[payload.topic_id]] if payload.topic_id else list(topic_links.values())
    subject = db.get(Subject, test.subject_id)
    provider = build_provider()
    context = retrieve(db, subject_id=test.subject_id, query=" ".join(topic.name for topic in topics), top_k=5)
    generated = provider.generate_practice_questions(
        subject=subject.name if subject else "",
        topics=[topic.name for topic in topics],
        count=payload.count,
        difficulty=(payload.difficulty or test.difficulty),
        context=context,
    )
    existing = db.execute(select(PracticeQuestion).where(PracticeQuestion.test_id == test.id)).scalars().all()
    if payload.replace_existing and existing:
        for question in existing:
            db.delete(question)
        db.flush()
        existing = []
    offset = len(existing)
    seen = {question.question_text.strip().lower() for question in existing}
    name_to_topic = {topic.name.lower(): topic for topic in topic_links.values()}
    added = 0
    for item in generated:
        text = str(item.get("question_text", "")).strip()
        if len(text) < 5 or text.lower() in seen:
            continue
        topic = name_to_topic.get(str(item.get("topic", "")).strip().lower()) or topics[added % len(topics)]
        options = [
            {"id": str(option.get("id", "")).strip()[:4], "text": str(option.get("text", "")).strip()[:600]}
            for option in (item.get("options") or [])
            if isinstance(option, dict) and str(option.get("id", "")).strip() and str(option.get("text", "")).strip()
        ]
        option_ids = [option["id"] for option in options]
        if len(options) < 2 or len(set(option_ids)) != len(option_ids):
            continue
        correct = [str(value) for value in (item.get("correct_answers") or []) if str(value) in option_ids][:1]
        if not correct:
            continue
        db.add(
            PracticeQuestion(
                test_id=test.id,
                topic_id=topic.id,
                question_text=text[:2000],
                question_type="SINGLE_CHOICE",
                options=options,
                correct_answers=correct,
                explanation=str(item.get("explanation", ""))[:2000],
                marks=1,
                difficulty=_enum_or(item.get("difficulty"), Difficulty, test.difficulty),
                bloom_level=_enum_or(item.get("bloom_level"), BloomLevel, BloomLevel.UNDERSTAND),
                position=offset + added,
            )
        )
        seen.add(text.lower())
        added += 1
        if added >= payload.count:
            break
    _sync_counts(db, test)
    db.flush()
    db.refresh(test)
    detail = test_detail(db, test, viewer=user)
    detail["_meta"] = {
        "generated": added,
        "mode": provider.mode,
        "provider": provider.name,
        "warning": (
            "DEMO generated practice questions - review before publishing."
            if provider.mode == "DEMO"
            else "AI generated - review before publishing."
        ),
    }
    return detail


# --- Attempts ---------------------------------------------------------------
def start_attempt(db: Session, student: User, test_id: str) -> dict[str, Any]:
    test = _student_test(db, student, test_id)
    if not test.questions:
        raise Conflict("This test has no questions yet.", "EMPTY_TEST")

    existing = db.execute(
        select(PracticeAttempt).where(
            PracticeAttempt.test_id == test.id,
            PracticeAttempt.student_id == student.id,
            PracticeAttempt.status == AttemptStatus.IN_PROGRESS,
        )
    ).scalars().all()
    for attempt in existing:
        if ensure_utc(attempt.expires_at) > _now():
            return _attempt_payload(attempt, test)
        _finalize_expired(db, attempt, test)

    state = availability(test)
    if state == "UPCOMING":
        raise Conflict("This practice test has not opened yet.", "TEST_NOT_OPEN")
    if state == "CLOSED":
        raise Conflict("This practice test has closed.", "TEST_CLOSED")
    if test.max_attempts:
        used = len(
            db.execute(
                select(PracticeAttempt.id).where(
                    PracticeAttempt.test_id == test.id, PracticeAttempt.student_id == student.id
                )
            ).all()
        )
        if used >= test.max_attempts:
            raise Conflict(
                f"You have used all {test.max_attempts} attempt(s) for this test.", "ATTEMPT_LIMIT_REACHED"
            )

    # An attempt never runs past the test's closing time.
    deadline = _now() + timedelta(minutes=test.duration_minutes)
    if test.closes_at:
        deadline = min(deadline, ensure_utc(test.closes_at))
    attempt = PracticeAttempt(
        test_id=test.id,
        student_id=student.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=_now(),
        expires_at=deadline,
        max_score=float(test.total_marks or sum(q.marks for q in test.questions)),
        draft_answers={},
    )
    db.add(attempt)
    db.flush()
    return _attempt_payload(attempt, test)


def _attempt_payload(attempt: PracticeAttempt, test: PracticeTest) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "test_id": test.id,
        "test_title": test.title,
        "duration_minutes": test.duration_minutes,
        "status": attempt.status,
        "started_at": attempt.started_at,
        "expires_at": attempt.expires_at,
        "server_time": _now(),
        "total_marks": test.total_marks or sum(question.marks for question in test.questions),
        "warning": (
            "Timer is enforced by the server. Answers are saved as you go; whatever is saved "
            "at the deadline is graded automatically."
        ),
        "saved_answers": dict(attempt.draft_answers or {}),
        "last_saved_at": attempt.last_saved_at,
        "questions": [
            _question_out(question, include_answers=False)
            for question in sorted(test.questions, key=lambda item: item.position)
        ],
    }


def _owned_attempt(db: Session, student: User, attempt_id: str) -> tuple[PracticeAttempt, PracticeTest]:
    attempt = db.get(PracticeAttempt, attempt_id)
    if attempt is None:
        raise NotFound("Attempt")
    if attempt.student_id != student.id:
        raise Forbidden("You can only access your own attempts.", code="ATTEMPT_NOT_OWNED")
    test = db.get(PracticeTest, attempt.test_id)
    if test is None:
        raise NotFound("Practice test")
    return attempt, test


def _deadline_passed(attempt: PracticeAttempt, *, grace: bool = True) -> bool:
    deadline = ensure_utc(attempt.expires_at) + timedelta(seconds=GRACE_SECONDS if grace else 0)
    return _now() > deadline


def get_attempt(db: Session, student: User, attempt_id: str) -> dict[str, Any]:
    attempt, test = _owned_attempt(db, student, attempt_id)
    if attempt.status == AttemptStatus.IN_PROGRESS and _deadline_passed(attempt):
        _finalize_expired(db, attempt, test)
    return _attempt_payload(attempt, test)


def _normalize_answers(test: PracticeTest, answers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep only answers to questions in this test, with option ids that exist."""
    questions = {question.id: question for question in test.questions}
    normalized: dict[str, dict[str, Any]] = {}
    for item in answers:
        question = questions.get(item.get("question_id"))
        if question is None:
            continue
        valid = {str(option.get("id")) for option in (question.options or [])}
        selected = [str(value) for value in item.get("selected") or [] if str(value) in valid]
        normalized[question.id] = {
            "selected": list(dict.fromkeys(selected)),
            "response_time_seconds": float(item.get("response_time_seconds") or 0),
        }
    return normalized


def autosave_attempt(db: Session, student: User, attempt_id: str, answers: list[dict[str, Any]]) -> dict[str, Any]:
    attempt, test = _owned_attempt(db, student, attempt_id)
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise Conflict("This attempt is already closed.", "ATTEMPT_CLOSED")
    if _deadline_passed(attempt):
        _finalize_expired(db, attempt, test)
        raise Conflict("Time is up. Your saved answers have been submitted.", "ATTEMPT_EXPIRED")
    merged = dict(attempt.draft_answers or {})
    merged.update(_normalize_answers(test, answers))
    attempt.draft_answers = merged
    attempt.last_saved_at = _now()
    db.flush()
    return {
        "saved": len(merged),
        "last_saved_at": attempt.last_saved_at,
        "expires_at": attempt.expires_at,
        "server_time": _now(),
    }


def _grade(
    db: Session,
    attempt: PracticeAttempt,
    test: PracticeTest,
    answers: dict[str, dict[str, Any]],
    *,
    status: AttemptStatus,
    submitted_at: datetime,
) -> None:
    questions = {question.id: question for question in test.questions}
    score = 0.0
    correct_count = 0
    topic_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"correct": 0, "attempted": 0, "marks": 0.0})
    difficulty_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"correct": 0, "attempted": 0})
    response_times: list[float] = []

    for question in questions.values():
        payload = answers.get(question.id)
        selected = list(payload["selected"]) if payload else []
        response_time = float(payload.get("response_time_seconds", 0)) if payload else 0.0
        is_correct = bool(selected) and set(selected) == set(question.correct_answers or [])
        awarded = float(question.marks) if is_correct else 0.0
        score += awarded
        correct_count += 1 if is_correct else 0
        response_times.append(response_time)
        db.add(
            PracticeAnswer(
                attempt_id=attempt.id,
                question_id=question.id,
                selected=selected,
                is_correct=is_correct,
                awarded_marks=awarded,
                response_time_seconds=response_time,
            )
        )
        topic_name = question.topic.name if question.topic else "Unknown"
        topic_stats[topic_name]["attempted"] += 1
        topic_stats[topic_name]["correct"] += 1 if is_correct else 0
        topic_stats[topic_name]["marks"] += awarded
        difficulty_stats[question.difficulty]["attempted"] += 1
        difficulty_stats[question.difficulty]["correct"] += 1 if is_correct else 0

    expires_at = ensure_utc(attempt.expires_at)
    started_at = ensure_utc(attempt.started_at)
    attempt.score = score
    attempt.max_score = float(sum(question.marks for question in questions.values()))
    attempt.accuracy = round(100.0 * correct_count / max(1, len(questions)), 2)
    attempt.status = status
    attempt.auto_submitted = status != AttemptStatus.SUBMITTED
    attempt.submitted_at = submitted_at
    attempt.duration_seconds = max(0, int((min(submitted_at, expires_at) - started_at).total_seconds()))
    db.flush()

    def breakdown(stats: dict[str, dict[str, Any]], key: str, with_marks: bool) -> list[dict[str, Any]]:
        rows = []
        for name, item in sorted(stats.items()):
            row = {
                key: name,
                "attempted": item["attempted"],
                "correct": item["correct"],
                "accuracy": round(100.0 * item["correct"] / item["attempted"], 2) if item["attempted"] else 0.0,
            }
            if with_marks:
                row["marks"] = item["marks"]
            rows.append(row)
        return rows

    result = db.execute(select(PracticeResult).where(PracticeResult.attempt_id == attempt.id)).scalar_one_or_none()
    if result is None:
        result = PracticeResult(attempt_id=attempt.id, test_id=test.id, student_id=attempt.student_id)
        db.add(result)
    result.score = score
    result.max_score = attempt.max_score
    result.percentage = round(100.0 * score / attempt.max_score, 2) if attempt.max_score else 0.0
    result.accuracy = attempt.accuracy
    result.avg_response_time = round(sum(response_times) / max(1, len(response_times)), 2)
    result.topic_breakdown = breakdown(topic_stats, "topic_name", True)
    result.difficulty_breakdown = breakdown(difficulty_stats, "difficulty", False)
    db.flush()


def _finalize_expired(db: Session, attempt: PracticeAttempt, test: PracticeTest) -> None:
    """The deadline passed without a submission: grade what was autosaved."""
    _grade(
        db,
        attempt,
        test,
        dict(attempt.draft_answers or {}),
        status=AttemptStatus.EXPIRED,
        submitted_at=ensure_utc(attempt.expires_at),
    )


def finalize_expired_attempts(db: Session, student_id: str | None = None) -> int:
    """Close every overdue attempt (optionally one student's), grading autosaved answers."""
    query = select(PracticeAttempt).where(PracticeAttempt.status == AttemptStatus.IN_PROGRESS)
    if student_id:
        query = query.where(PracticeAttempt.student_id == student_id)
    closed = 0
    for attempt in db.execute(query).scalars().all():
        if _deadline_passed(attempt):
            test = db.get(PracticeTest, attempt.test_id)
            if test is not None:
                _finalize_expired(db, attempt, test)
                closed += 1
    return closed


def submit_attempt(db: Session, student: User, attempt_id: str, answers: list[dict[str, Any]]) -> dict[str, Any]:
    attempt, test = _owned_attempt(db, student, attempt_id)
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise Conflict("This attempt was already submitted.", "ATTEMPT_CLOSED")
    if _deadline_passed(attempt):
        # Late: the payload is ignored. Only what was autosaved before the
        # deadline counts, so the timer cannot be bypassed by submitting later.
        _finalize_expired(db, attempt, test)
    else:
        merged = dict(attempt.draft_answers or {})
        merged.update(_normalize_answers(test, answers))
        attempt.draft_answers = merged
        _grade(db, attempt, test, merged, status=AttemptStatus.SUBMITTED, submitted_at=_now())
    return get_result(db, student, attempt.id)


def get_result(db: Session, requester: User, attempt_id: str) -> dict[str, Any]:
    attempt = db.get(PracticeAttempt, attempt_id)
    if attempt is None:
        raise NotFound("Attempt")
    if requester.role == Role.STUDENT:
        if attempt.student_id != requester.id:
            raise Forbidden("You can only access your own results.", code="RESULT_NOT_OWNED")
        test = db.get(PracticeTest, attempt.test_id)
        if test is None:
            raise NotFound("Practice test")
    else:
        test = ensure_view_test(db, requester, attempt.test_id)
    if attempt.status == AttemptStatus.IN_PROGRESS:
        if _deadline_passed(attempt):
            _finalize_expired(db, attempt, test)
        else:
            raise Conflict("This attempt has not been submitted yet.", "ATTEMPT_OPEN")
    result = db.execute(select(PracticeResult).where(PracticeResult.attempt_id == attempt.id)).scalar_one_or_none()
    if result is None:
        raise NotFound("Result")
    answers = {
        answer.question_id: answer
        for answer in db.execute(select(PracticeAnswer).where(PracticeAnswer.attempt_id == attempt.id)).scalars()
    }
    review = []
    for question in sorted(test.questions, key=lambda item: item.position):
        answer = answers.get(question.id)
        review.append(
            {
                "question_id": question.id,
                "question_text": question.question_text,
                "question_type": question.question_type,
                "options": question.options or [],
                "selected": list(answer.selected) if answer else [],
                "correct_answers": list(question.correct_answers or []),
                "is_correct": bool(answer.is_correct) if answer else False,
                "awarded_marks": float(answer.awarded_marks) if answer else 0.0,
                "marks": question.marks,
                "explanation": question.explanation,
                "topic_name": question.topic.name if question.topic else "",
                "difficulty": question.difficulty,
                "response_time_seconds": float(answer.response_time_seconds) if answer else 0.0,
            }
        )
    student = db.get(User, attempt.student_id)
    return {
        "id": result.id,
        "attempt_id": attempt.id,
        "test_id": test.id,
        "test_title": test.title,
        "subject_name": _subject_name(db, test.subject_id),
        "score": round(result.score, 2),
        "max_score": round(result.max_score, 2),
        "percentage": round(result.percentage, 2),
        "accuracy": round(result.accuracy, 2),
        "avg_response_time": round(result.avg_response_time, 2),
        "created_at": result.created_at,
        "topic_breakdown": result.topic_breakdown,
        "difficulty_breakdown": result.difficulty_breakdown,
        "review": review,
        "student_name": student.full_name if student and requester.role != Role.STUDENT else None,
        "auto_submitted": attempt.auto_submitted,
    }


def get_test_analytics(db: Session, user: User, test_id: str) -> dict[str, Any]:
    test = ensure_view_test(db, user, test_id)
    finalize_expired_attempts_for_test(db, test)
    return teacher_test_analytics(db, test)


def finalize_expired_attempts_for_test(db: Session, test: PracticeTest) -> None:
    for attempt in db.execute(
        select(PracticeAttempt).where(
            PracticeAttempt.test_id == test.id, PracticeAttempt.status == AttemptStatus.IN_PROGRESS
        )
    ).scalars():
        if _deadline_passed(attempt):
            _finalize_expired(db, attempt, test)


def student_history(db: Session, student: User) -> list[dict[str, Any]]:
    finalize_expired_attempts(db, student.id)
    return attempt_history(db, student.id)
