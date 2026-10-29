"""Analytics and recommendation engine (practice module only)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutils import ensure_utc
from app.models.enums import ExamStatus, PaperState, PracticeTestStatus, Role
from app.models.tables import (
    AuditLog,
    InternalExam,
    PaperRelease,
    PracticeAnswer,
    PracticeAttempt,
    PracticeQuestion,
    PracticeResult,
    PracticeTest,
    SecurityEvent,
    Subject,
    Topic,
    User,
)


def _pct(correct: int, attempted: int) -> float:
    return round(100.0 * correct / attempted, 2) if attempted else 0.0


def _weak_label(accuracy: float) -> str:
    if accuracy >= 80:
        return "STRONG"
    if accuracy >= 60:
        return "DEVELOPING"
    return "WEAK"


def topic_performance(db: Session, student_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            Topic.id,
            Topic.name,
            Subject.name,
            PracticeAnswer.is_correct,
            PracticeAnswer.response_time_seconds,
        )
        .join(PracticeAttempt, PracticeAnswer.attempt_id == PracticeAttempt.id)
        .join(PracticeQuestion, PracticeAnswer.question_id == PracticeQuestion.id)
        .join(Topic, PracticeQuestion.topic_id == Topic.id)
        .join(Subject, Topic.subject_id == Subject.id)
        .where(PracticeAttempt.student_id == student_id)
    ).all()
    buckets: dict[str, dict[str, Any]] = {}
    for topic_id, topic_name, subject_name, correct, response_time in rows:
        bucket = buckets.setdefault(
            topic_id,
            {
                "topic_id": topic_id,
                "topic_name": topic_name,
                "subject_name": subject_name,
                "attempted": 0,
                "correct": 0,
                "time_total": 0.0,
            },
        )
        bucket["attempted"] += 1
        bucket["correct"] += 1 if correct else 0
        bucket["time_total"] += float(response_time or 0)
    result = []
    for bucket in buckets.values():
        accuracy = _pct(bucket["correct"], bucket["attempted"])
        result.append(
            {
                "topic_id": bucket["topic_id"],
                "topic_name": bucket["topic_name"],
                "subject_name": bucket["subject_name"],
                "attempted": bucket["attempted"],
                "correct": bucket["correct"],
                "accuracy": accuracy,
                "avg_time": round(bucket["time_total"] / max(1, bucket["attempted"]), 2),
                "strength": _weak_label(accuracy),
            }
        )
    return sorted(result, key=lambda item: (item["accuracy"], -item["attempted"]))


def difficulty_performance(db: Session, student_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            PracticeQuestion.difficulty,
            PracticeAnswer.is_correct,
            PracticeAnswer.response_time_seconds,
        )
        .join(PracticeAttempt, PracticeAnswer.attempt_id == PracticeAttempt.id)
        .join(PracticeQuestion, PracticeAnswer.question_id == PracticeQuestion.id)
        .where(PracticeAttempt.student_id == student_id)
    ).all()
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"attempted": 0, "correct": 0, "time": 0.0})
    for difficulty, correct, response_time in rows:
        bucket = buckets[difficulty]
        bucket["attempted"] += 1
        bucket["correct"] += 1 if correct else 0
        bucket["time"] += float(response_time or 0)
    return [
        {
            "difficulty": difficulty,
            "attempted": int(bucket["attempted"]),
            "correct": int(bucket["correct"]),
            "accuracy": _pct(int(bucket["correct"]), int(bucket["attempted"])),
            "avg_time": round(bucket["time"] / max(1, int(bucket["attempted"])), 2),
        }
        for difficulty, bucket in sorted(buckets.items())
    ]


def recommendations_for(db: Session, student_id: str, weak_topics: list[dict]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for topic in weak_topics[:3]:
        suggested = db.execute(
            select(PracticeTest)
            .join(PracticeTest.topics)
            .where(
                PracticeTest.status == PracticeTestStatus.PUBLISHED,
                PracticeTest.topics.any(topic_id=topic["topic_id"]),
            )
            .order_by(PracticeTest.created_at.desc())
            .limit(2)
        ).scalars().all()
        attempted_ids = set(
            db.execute(
                select(PracticeAttempt.test_id).where(PracticeAttempt.student_id == student_id)
            ).scalars()
        )
        recommendations.append(
            {
                "title": f"Strengthen {topic['topic_name']}",
                "topic_id": topic["topic_id"],
                "topic_name": topic["topic_name"],
                "accuracy": topic["accuracy"],
                "priority": "HIGH" if topic["accuracy"] < 45 else "MEDIUM",
                "action": (
                    f"Practice {topic['topic_name']} questions at medium difficulty, then retry one "
                    "test to confirm improvement."
                ),
                "practice_tests": [
                    {
                        "id": test.id,
                        "title": test.title,
                        "duration_minutes": test.duration_minutes,
                        "question_count": test.question_count,
                        "completed": test.id in attempted_ids,
                    }
                    for test in suggested
                ],
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "title": "Keep momentum",
                "topic_name": "",
                "accuracy": 0,
                "priority": "LOW",
                "action": "No weak topic found yet. Attempt another practice test to widen your analytics.",
                "practice_tests": [],
            }
        )
    return recommendations


def student_analytics(db: Session, student_id: str) -> dict[str, Any]:
    results = (
        db.execute(
            select(PracticeResult)
            .where(PracticeResult.student_id == student_id)
            .order_by(PracticeResult.created_at.asc())
        )
        .scalars()
        .all()
    )
    answers = db.execute(
        select(PracticeAnswer, PracticeAttempt)
        .join(PracticeAttempt, PracticeAnswer.attempt_id == PracticeAttempt.id)
        .where(PracticeAttempt.student_id == student_id)
    ).all()
    topics = topic_performance(db, student_id)
    difficulties = difficulty_performance(db, student_id)
    percentages = [round(result.percentage, 2) for result in results]
    total_answers = len(answers)
    correct = sum(1 for answer, _ in answers if answer.is_correct)
    response_times = [float(answer.response_time_seconds or 0) for answer, _ in answers]
    recent = percentages[-3:]
    previous = percentages[-6:-3]
    improvement = round(mean(recent) - mean(previous), 2) if recent and previous else 0.0
    weak_topics = [topic for topic in topics if topic["attempted"] >= 1 and topic["accuracy"] < 60]
    trend = [
        {
            "attempt_id": result.attempt_id,
            "date": result.created_at.isoformat(),
            "percentage": round(result.percentage, 2),
            "accuracy": round(result.accuracy, 2),
        }
        for result in results[-10:]
    ]
    return {
        "total_attempts": len(results),
        "average_percentage": round(mean(percentages), 2) if percentages else 0.0,
        "best_percentage": max(percentages) if percentages else 0.0,
        "overall_accuracy": _pct(correct, total_answers),
        "avg_response_time": round(mean(response_times), 2) if response_times else 0.0,
        "improvement": improvement,
        "weak_topics": weak_topics,
        "topic_performance": topics,
        "difficulty_performance": difficulties,
        "trend": trend,
        "recommendations": recommendations_for(db, student_id, weak_topics),
    }


def attempt_history(db: Session, student_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(PracticeAttempt, PracticeTest, Subject.name)
        .join(PracticeTest, PracticeAttempt.test_id == PracticeTest.id)
        .join(Subject, PracticeTest.subject_id == Subject.id)
        .where(PracticeAttempt.student_id == student_id)
        .order_by(PracticeAttempt.started_at.desc())
        .limit(60)
    ).all()
    return [
        {
            "attempt_id": attempt.id,
            "test_id": test.id,
            "test_title": test.title,
            "subject_name": subject_name,
            "started_at": attempt.started_at,
            "submitted_at": attempt.submitted_at,
            "score": round(attempt.score, 2),
            "max_score": round(attempt.max_score, 2),
            "percentage": round(100.0 * attempt.score / attempt.max_score, 2) if attempt.max_score else 0.0,
            "accuracy": round(attempt.accuracy, 2),
            "status": attempt.status,
        }
        for attempt, test, subject_name in rows
    ]


def teacher_test_analytics(db: Session, test: PracticeTest) -> dict[str, Any]:
    attempts = (
        db.execute(select(PracticeAttempt).where(PracticeAttempt.test_id == test.id)).scalars().all()
    )
    submitted = [attempt for attempt in attempts if attempt.status in {"SUBMITTED", "AUTO_SUBMITTED"}]
    results = (
        db.execute(select(PracticeResult).where(PracticeResult.test_id == test.id)).scalars().all()
    )
    topic_buckets: dict[str, dict[str, Any]] = {}
    student_rows: dict[str, dict[str, Any]] = {}
    for result in results:
        for row in result.topic_breakdown or []:
            name = row.get("topic_name", "Unknown")
            bucket = topic_buckets.setdefault(name, {"topic_name": name, "attempted": 0, "correct": 0})
            bucket["attempted"] += int(row.get("attempted", 0))
            bucket["correct"] += int(row.get("correct", 0))
        student = db.get(User, result.student_id)
        student_rows[result.student_id] = {
            "student_id": result.student_id,
            "student_name": student.full_name if student else "Unknown",
            "student_email": student.email if student else "",
            "attempts": 1,
            "best_percentage": round(result.percentage, 2),
            "last_attempt": result.created_at,
        }
    for name, bucket in topic_buckets.items():
        bucket["accuracy"] = _pct(bucket["correct"], bucket["attempted"])
    return {
        "test_id": test.id,
        "test_title": test.title,
        "attempts": len(attempts),
        "average_percentage": round(mean([r.percentage for r in results]), 2) if results else 0.0,
        "average_accuracy": round(mean([r.accuracy for r in results]), 2) if results else 0.0,
        "completion_rate": round(100.0 * len(submitted) / len(attempts), 2) if attempts else 0.0,
        "topic_breakdown": sorted(topic_buckets.values(), key=lambda item: item["topic_name"]),
        "student_rows": sorted(student_rows.values(), key=lambda item: item["student_name"]),
    }


def student_dashboard(db: Session, student: User) -> dict[str, Any]:
    analytics = student_analytics(db, student.id)
    published = (
        db.execute(
            select(PracticeTest).where(PracticeTest.status == PracticeTestStatus.PUBLISHED)
        ).scalars().all()
    )
    attempted = set(
        db.execute(select(PracticeAttempt.test_id).where(PracticeAttempt.student_id == student.id)).scalars()
    )
    recent = attempt_history(db, student.id)[:5]
    return {
        "tests_available": len([test for test in published if test.id not in attempted]),
        "tests_attempted": len(attempted),
        "summary": {
            "total_attempts": analytics["total_attempts"],
            "average_percentage": analytics["average_percentage"],
            "best_percentage": analytics["best_percentage"],
            "overall_accuracy": analytics["overall_accuracy"],
            "improvement": analytics["improvement"],
        },
        "weak_topics": analytics["weak_topics"][:3],
        "recent_attempts": recent,
        "trend": analytics["trend"],
        "recommendations": analytics["recommendations"][:2],
    }


def teacher_dashboard(db: Session, teacher: User) -> dict[str, Any]:
    from app.services.academic_service import teacher_subject_ids

    subject_ids = teacher_subject_ids(db, teacher.id)
    tests = (
        db.execute(select(PracticeTest).where(PracticeTest.created_by == teacher.id)).scalars().all()
    )
    attempts = (
        db.execute(
            select(PracticeAttempt).where(PracticeAttempt.test_id.in_([test.id for test in tests]))
        ).scalars().all()
        if tests
        else []
    )
    results = (
        db.execute(
            select(PracticeResult).where(PracticeResult.test_id.in_([test.id for test in tests]))
        ).scalars().all()
        if tests
        else []
    )
    exams = (
        db.execute(
            select(InternalExam)
            .where(InternalExam.created_by == teacher.id)
            .order_by(InternalExam.created_at.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    secured = db.execute(
        select(InternalExam.id)
        .where(
            InternalExam.created_by == teacher.id,
            InternalExam.status == ExamStatus.SECURED,
        )
    ).scalars().all()
    return {
        "subjects": len(subject_ids),
        "practice_tests": len(tests),
        "published_tests": len([test for test in tests if test.status == PracticeTestStatus.PUBLISHED]),
        "total_attempts": len(attempts),
        "average_percentage": round(mean([r.percentage for r in results]), 2) if results else 0.0,
        "active_students": len({attempt.student_id for attempt in attempts}),
        "secured_papers": len(secured),
        "recent_exams": [
            {
                "id": exam.id,
                "title": exam.title,
                "exam_type": exam.exam_type,
                "exam_date": exam.exam_date,
                "status": exam.status,
                "total_marks": exam.total_marks,
            }
            for exam in exams
        ],
    }


def hod_dashboard(db: Session, hod: User) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    exams = db.execute(select(InternalExam).order_by(InternalExam.exam_date)).scalars().all()
    releases = db.execute(select(PaperRelease).order_by(PaperRelease.authorized_at.desc())).scalars().all()
    window = max(1, 30)
    security = (
        db.execute(
            select(SecurityEvent)
            .where(SecurityEvent.created_at >= now - timedelta(days=window))
            .order_by(SecurityEvent.created_at.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    upcoming = [exam for exam in exams if ensure_utc(exam.exam_date) >= now - timedelta(hours=6)]
    secured_exams = [exam for exam in exams if exam.status == ExamStatus.SECURED]
    return {
        "upcoming_exams": len(upcoming),
        "secured_papers": len(secured_exams),
        "released_papers": len([release for release in releases if release.status == "RELEASED"]),
        "pending_release": len(
            [exam for exam in secured_exams if ensure_utc(exam.exam_date) <= now + timedelta(hours=24)]
        ),
        "security_events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "severity": event.severity,
                "summary": event.summary,
                "created_at": event.created_at,
                "resolved": event.resolved,
            }
            for event in security
        ],
        "recent_releases": [
            {
                "id": release.id,
                "paper_id": release.paper_id,
                "status": release.status,
                "authorized_at": release.authorized_at,
                "print_count": release.print_count,
            }
            for release in releases[:5]
        ],
        "next_exams": [
            {
                "id": exam.id,
                "title": exam.title,
                "exam_type": exam.exam_type,
                "exam_date": exam.exam_date,
                "status": exam.status,
                "total_marks": exam.total_marks,
            }
            for exam in upcoming[:6]
        ],
    }
