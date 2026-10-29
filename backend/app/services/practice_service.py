"""Online practice module (fully independent from the secure internal exam workflow)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.provider import build_provider
from app.analytics.engine import attempt_history, teacher_test_analytics
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.timeutils import ensure_utc
from app.models.enums import AttemptStatus, PracticeTestStatus, Role
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
    PracticeQuestionCreate,
    PracticeTestCreate,
    PracticeTestUpdate,
    GeneratePracticeQuestionsRequest,
)
from app.services.academic_service import ensure_subject_access, teacher_has_subject

GRACE_SECONDS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _subject_name(db: Session, subject_id: str) -> str:
    subject = db.get(Subject, subject_id)
    return subject.name if subject else ""


def _test_out(
    db: Session,
    test: PracticeTest,
    *,
    student_id: str | None = None,
) -> dict[str, Any]:
    attempts = db.execute(
        select(PracticeAttempt).where(PracticeAttempt.test_id == test.id)
    ).scalars().all()
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


def create_test(db: Session, teacher: User, payload: PracticeTestCreate) -> dict[str, Any]:
    subject = ensure_subject_access(db, teacher, payload.subject_id)
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
    )
    db.add(test)
    db.flush()
    for topic_id in payload.topic_ids:
        db.add(PracticeTestTopic(test_id=test.id, topic_id=topic_id))
    db.flush()
    for index, question in enumerate(payload.questions):
        _add_question(db, test, question, index)
    _sync_counts(db, test)
    db.flush()
    return {"test_id": test.id, "detail": test_detail(db, test, include_answers=True)}


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
    questions = db.execute(
        select(PracticeQuestion).where(PracticeQuestion.test_id == test.id)
    ).scalars().all()
    test.question_count = len(questions)
    test.total_marks = sum(question.marks for question in questions)


def list_tests(db: Session, user: User, subject_id: str | None = None) -> list[dict[str, Any]]:
    query = select(PracticeTest)
    if user.role == Role.TEACHER:
        query = query.where(PracticeTest.created_by == user.id)
    elif user.role == Role.STUDENT:
        query = query.where(PracticeTest.status == PracticeTestStatus.PUBLISHED)
    if subject_id:
        query = query.where(PracticeTest.subject_id == subject_id)
    tests = db.execute(query.order_by(PracticeTest.created_at.desc())).scalars().all()
    return [
        _test_out(db, test, student_id=user.id if user.role == Role.STUDENT else None)
        for test in tests
    ]


def get_test(db: Session, user: User, test_id: str, *, include_answers: bool = False) -> dict[str, Any]:
    test = db.get(PracticeTest, test_id)
    if test is None:
        raise NotFound("Practice test")
    if user.role == Role.STUDENT and test.status != PracticeTestStatus.PUBLISHED:
        raise Forbidden("This practice test is not published.", code="TEST_NOT_PUBLISHED")
    if user.role == Role.TEACHER and test.created_by != user.id:
        raise Forbidden("You can only view your own practice tests.", code="NOT_TEST_OWNER")
    base = _test_out(db, test, student_id=user.id if user.role == Role.STUDENT else None)
    base["questions"] = [
        _question_out(question, include_answers=include_answers)
        for question in sorted(test.questions, key=lambda item: item.position)
    ]
    return base


def test_detail(db: Session, test: PracticeTest, *, include_answers: bool = True) -> dict[str, Any]:
    base = _test_out(db, test)
    base["questions"] = [
        _question_out(question, include_answers=include_answers)
        for question in sorted(test.questions, key=lambda item: item.position)
    ]
    return base


def update_test(db: Session, teacher: User, test_id: str, payload: PracticeTestUpdate) -> dict[str, Any]:
    test = _owned_test(db, teacher, test_id)
    attempts = db.execute(
        select(PracticeAttempt).where(PracticeAttempt.test_id == test.id)
    ).scalars().all()
    if attempts and test.status == PracticeTestStatus.PUBLISHED:
        raise Conflict(
            "A published test with attempts cannot be edited. Archive it and create a new test.",
            "TEST_LOCKED",
        )
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(test, field, value)
    db.flush()
    return test_detail(db, test)


def set_status(db: Session, teacher: User, test_id: str, status: str) -> dict[str, Any]:
    test = _owned_test(db, teacher, test_id)
    if status == PracticeTestStatus.PUBLISHED:
        _sync_counts(db, test)
        if test.question_count == 0:
            raise Conflict("Add at least one question before publishing.", "EMPTY_TEST")
        test.published_at = _now()
    test.status = status
    db.flush()
    return test_detail(db, test)


def _owned_test(db: Session, teacher: User, test_id: str) -> PracticeTest:
    test = db.get(PracticeTest, test_id)
    if test is None:
        raise NotFound("Practice test")
    if test.created_by != teacher.id:
        raise Forbidden("You can only manage your own practice tests.", code="NOT_TEST_OWNER")
    return test


def add_questions(
    db: Session, teacher: User, test_id: str, payloads: list[PracticeQuestionCreate]
) -> dict[str, Any]:
    test = _owned_test(db, teacher, test_id)
    if test.status != PracticeTestStatus.DRAFT:
        raise Conflict("Questions can only be added while the test is a draft.", "TEST_NOT_DRAFT")
    existing = db.execute(
        select(PracticeQuestion).where(PracticeQuestion.test_id == test.id)
    ).scalars().all()
    offset = len(existing)
    seen = {question.question_text.strip().lower() for question in existing}
    for index, payload in enumerate(payloads):
        if payload.question_text.strip().lower() in seen:
            continue
        _add_question(db, test, payload, offset + index)
        seen.add(payload.question_text.strip().lower())
    _sync_counts(db, test)
    db.flush()
    return test_detail(db, test)


def generate_questions(
    db: Session, teacher: User, test_id: str, payload: GeneratePracticeQuestionsRequest
) -> dict[str, Any]:
    test = _owned_test(db, teacher, test_id)
    if test.status != PracticeTestStatus.DRAFT:
        raise Conflict("Generate questions while the test is a draft.", "TEST_NOT_DRAFT")
    topic_links = {link.topic_id: link.topic for link in test.topics if link.topic}
    if not topic_links:
        raise Conflict("Select at least one topic first.", "NO_TOPICS")
    topics = [topic_links[payload.topic_id]] if payload.topic_id else list(topic_links.values())
    if payload.topic_id and payload.topic_id not in topic_links:
        raise Conflict("That topic is not part of this test.", "INVALID_TOPIC")
    subject = db.get(Subject, test.subject_id)
    provider = build_provider()
    context = retrieve(
        db,
        subject_id=test.subject_id,
        query=" ".join(topic.name for topic in topics),
        top_k=5,
    )
    generated = provider.generate_practice_questions(
        subject=subject.name if subject else "",
        topics=[topic.name for topic in topics],
        count=payload.count,
        difficulty=(payload.difficulty or test.difficulty),
        context=context,
    )
    existing = db.execute(
        select(PracticeQuestion).where(PracticeQuestion.test_id == test.id)
    ).scalars().all()
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
        if not text or text.lower() in seen:
            continue
        topic = name_to_topic.get(str(item.get("topic", "")).strip().lower())
        if topic is None:
            topic = topics[added % len(topics)]
        options = item.get("options") or []
        if len(options) < 2:
            continue
        correct = [str(value) for value in (item.get("correct_answers") or [])]
        option_ids = {str(option.get("id")) for option in options}
        correct = [value for value in correct if value in option_ids][:1]
        if not correct:
            continue
        db.add(
            PracticeQuestion(
                test_id=test.id,
                topic_id=topic.id,
                question_text=text,
                question_type="SINGLE_CHOICE",
                options=options,
                correct_answers=correct,
                explanation=str(item.get("explanation", ""))[:2000],
                marks=1,
                difficulty=str(item.get("difficulty", test.difficulty)).upper(),
                bloom_level=str(item.get("bloom_level", "UNDERSTAND")).upper(),
                position=offset + added,
            )
        )
        seen.add(text.lower())
        added += 1
        if added >= payload.count:
            break
    _sync_counts(db, test)
    db.flush()
    detail = test_detail(db, test)
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


def start_attempt(db: Session, student: User, test_id: str) -> dict[str, Any]:
    test = db.get(PracticeTest, test_id)
    if test is None:
        raise NotFound("Practice test")
    if test.status != PracticeTestStatus.PUBLISHED:
        raise Forbidden("This practice test is not published.", code="TEST_NOT_PUBLISHED")
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
        _auto_submit_expired(db, attempt, test)

    attempt = PracticeAttempt(
        test_id=test.id,
        student_id=student.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=_now(),
        expires_at=_now() + timedelta(minutes=test.duration_minutes),
        max_score=float(test.total_marks or sum(q.marks for q in test.questions)),
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
            "Timer is validated on the server. Answers submitted after the deadline are auto-submitted."
        ),
        "questions": [
            _question_out(question, include_answers=False)
            for question in sorted(test.questions, key=lambda item: item.position)
        ],
    }


def get_attempt(db: Session, student: User, attempt_id: str) -> dict[str, Any]:
    attempt = db.get(PracticeAttempt, attempt_id)
    if attempt is None:
        raise NotFound("Attempt")
    if attempt.student_id != student.id:
        raise Forbidden("You can only access your own attempts.", code="ATTEMPT_NOT_OWNED")
    test = db.get(PracticeTest, attempt.test_id)
    if test is None:
        raise NotFound("Practice test")
    if attempt.status == AttemptStatus.IN_PROGRESS and ensure_utc(attempt.expires_at) < _now():
        _auto_submit_expired(db, attempt, test)
    return _attempt_payload(attempt, test)


def _auto_submit_expired(db: Session, attempt: PracticeAttempt, test: PracticeTest) -> None:
    attempt.status = AttemptStatus.EXPIRED
    attempt.submitted_at = attempt.expires_at
    attempt.auto_submitted = True
    attempt.duration_seconds = int(
        (ensure_utc(attempt.expires_at) - ensure_utc(attempt.started_at)).total_seconds()
    )
    attempt.score = 0
    attempt.accuracy = 0
    db.add(
        PracticeResult(
            attempt_id=attempt.id,
            test_id=test.id,
            student_id=attempt.student_id,
            score=0,
            max_score=attempt.max_score,
            percentage=0,
            accuracy=0,
            avg_response_time=0,
            topic_breakdown=[],
            difficulty_breakdown=[],
        )
    )
    db.flush()


def submit_attempt(
    db: Session, student: User, attempt_id: str, answers: list[dict[str, Any]]
) -> dict[str, Any]:
    attempt = db.get(PracticeAttempt, attempt_id)
    if attempt is None:
        raise NotFound("Attempt")
    if attempt.student_id != student.id:
        raise Forbidden("You can only submit your own attempts.", code="ATTEMPT_NOT_OWNED")
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise Conflict("This attempt was already submitted.", "ATTEMPT_CLOSED")
    test = db.get(PracticeTest, attempt.test_id)
    if test is None:
        raise NotFound("Practice test")

    now = _now()
    expires_at = ensure_utc(attempt.expires_at)
    started_at = ensure_utc(attempt.started_at)
    late = now > expires_at + timedelta(seconds=GRACE_SECONDS)
    questions = {question.id: question for question in test.questions}
    submitted = {item["question_id"]: item for item in answers if item["question_id"] in questions}

    score = 0.0
    correct_count = 0
    topic_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"correct": 0, "attempted": 0, "marks": 0.0}
    )
    difficulty_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"correct": 0, "attempted": 0}
    )
    response_times: list[float] = []

    for question in questions.values():
        payload = submitted.get(question.id)
        selected = list(payload["selected"]) if payload else []
        response_time = float(payload.get("response_time_seconds", 0)) if payload else 0.0
        correct_set = set(question.correct_answers or [])
        is_correct = bool(selected) and set(selected) == correct_set
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

    attempt.score = score
    attempt.max_score = float(sum(question.marks for question in questions.values()))
    attempt.accuracy = round(100.0 * correct_count / max(1, len(questions)), 2)
    attempt.status = AttemptStatus.AUTO_SUBMITTED if late else AttemptStatus.SUBMITTED
    attempt.auto_submitted = late
    attempt.submitted_at = now
    attempt.duration_seconds = max(0, int((min(now, expires_at) - started_at).total_seconds()))
    db.flush()

    percentage = round(100.0 * score / attempt.max_score, 2) if attempt.max_score else 0.0
    result = db.execute(
        select(PracticeResult).where(PracticeResult.attempt_id == attempt.id)
    ).scalar_one_or_none()
    topic_breakdown = [
        {
            "topic_name": name,
            "attempted": stats["attempted"],
            "correct": stats["correct"],
            "accuracy": round(100.0 * stats["correct"] / stats["attempted"], 2)
            if stats["attempted"]
            else 0.0,
            "marks": stats["marks"],
        }
        for name, stats in sorted(topic_stats.items())
    ]
    difficulty_breakdown = [
        {
            "difficulty": name,
            "attempted": stats["attempted"],
            "correct": stats["correct"],
            "accuracy": round(100.0 * stats["correct"] / stats["attempted"], 2)
            if stats["attempted"]
            else 0.0,
        }
        for name, stats in sorted(difficulty_stats.items())
    ]
    avg_response = round(sum(response_times) / max(1, len(response_times)), 2)
    if result is None:
        result = PracticeResult(
            attempt_id=attempt.id,
            test_id=test.id,
            student_id=student.id,
        )
        db.add(result)
    result.score = score
    result.max_score = attempt.max_score
    result.percentage = percentage
    result.accuracy = attempt.accuracy
    result.avg_response_time = avg_response
    result.topic_breakdown = topic_breakdown
    result.difficulty_breakdown = difficulty_breakdown
    db.flush()
    return get_result(db, student, attempt.id, staff_view=True)


def get_result(
    db: Session, requester: User, attempt_id: str, *, staff_view: bool = False
) -> dict[str, Any]:
    attempt = db.get(PracticeAttempt, attempt_id)
    if attempt is None:
        raise NotFound("Attempt")
    if requester.role == Role.STUDENT and attempt.student_id != requester.id:
        raise Forbidden("You can only access your own results.", code="RESULT_NOT_OWNED")
    test = db.get(PracticeTest, attempt.test_id)
    if test is None:
        raise NotFound("Practice test")
    if requester.role == Role.TEACHER:
        if not teacher_has_subject(db, requester, test.subject_id):
            raise Forbidden("You can only view results for your assigned subjects.")
    if attempt.status == AttemptStatus.IN_PROGRESS:
        raise Conflict("This attempt has not been submitted yet.", "ATTEMPT_OPEN")
    result = db.execute(
        select(PracticeResult).where(PracticeResult.attempt_id == attempt.id)
    ).scalar_one_or_none()
    if result is None:
        raise NotFound("Result")
    answers = {
        answer.question_id: answer
        for answer in db.execute(
            select(PracticeAnswer).where(PracticeAnswer.attempt_id == attempt.id)
        ).scalars()
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
        "student_name": student.full_name if student and staff_view else None,
        "auto_submitted": attempt.auto_submitted,
    }


def get_test_analytics(db: Session, teacher: User, test_id: str) -> dict[str, Any]:
    test = _owned_test(db, teacher, test_id)
    return teacher_test_analytics(db, test)


def student_history(db: Session, student: User) -> list[dict[str, Any]]:
    return attempt_history(db, student.id)
