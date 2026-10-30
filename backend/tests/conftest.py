"""Test configuration: isolated SQLite database, storage and seeded fixtures."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_DIR = Path(tempfile.mkdtemp(prefix="praxis-tests-"))
os.environ.update(
    {
        "ENV": "test",
        "DEBUG": "false",
        "DEMO_MODE": "true",
        "SEED_ON_STARTUP": "false",
        "JWT_SECRET": "test-secret-value-that-is-long-enough-123456",
        "MASTER_KEY_ID": "test-key-1",
        # Set TEST_DATABASE_URL to an empty PostgreSQL database to run the suite there.
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{TEST_DIR / 'test.db'}",
        "STORAGE_BACKEND": "local",
        "STORAGE_LOCAL_PATH": str(TEST_DIR / "storage"),
        "GEN_INLINE": "false",
        "RATE_LIMIT_LOGIN": "100",
        "RATE_LIMIT_MFA": "100",
        "RATE_LIMIT_GENERAL": "100000",
        "RATE_LIMIT_GENERATION": "1000",
        "RATE_LIMIT_UPLOAD": "1000",
        "RELEASE_WINDOW_MINUTES": "120",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import hash_password, new_totp_secret, seal_secret  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import (  # noqa: E402
    BloomLevel,
    Difficulty,
    ExamStatus,
    QuestionType,
    Role,
)
from app.models.tables import ExamConfiguration, InternalExam, Subject, Topic, User  # noqa: E402
from app.schemas.exam import ExamConfigCreate, ExamTopicIn, PatternItem  # noqa: E402
from app.schemas.practice import OptionIn, PracticeQuestionCreate, PracticeTestCreate  # noqa: E402
from app.services import academic_service, exam_service, practice_service, syllabus_service  # noqa: E402

PASSWORD = "TestPass#123"

SYLLABUS_TEXT = """
OPERATING SYSTEMS AND DATABASE SYSTEMS - REVISION NOTES

Normalization removes redundancy. First normal form requires atomic values. Second normal
form removes partial dependencies. Third normal form removes transitive dependencies.
Boyce-Codd normal form requires every determinant to be a superkey. Functional dependencies
are derived with Armstrong's axioms. Lossless join decomposition preserves information.

Transactions provide atomicity, consistency, isolation and durability. A precedence graph
detects conflict serializability; a cycle indicates a non-serializable schedule. Two-phase
locking has growing and shrinking phases. Write-ahead logging supports recovery.

Process scheduling chooses a ready process. Round robin uses a time quantum. Priority
scheduling can starve processes unless aging is used. A page fault occurs when the referenced
page is not resident. Thrashing follows an undersized resident set. Journaling file systems
replay intent records after a crash. Deadlock requires mutual exclusion, hold and wait,
no preemption and circular wait. The Banker's algorithm avoids unsafe states.
"""


def _token_headers(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    # A fresh client avoids carrying another role's cookies into the MFA POST,
    # which would (correctly) trip the CSRF middleware.
    client = TestClient(client.app)
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    data = response.json()
    if data.get("mfa_required"):
        code = client.post(
            "/api/auth/mfa/challenge-code", json={"mfa_token": data["mfa_token"]}
        ).json()["code"]
        response = client.post(
            "/api/auth/mfa/verify", json={"mfa_token": data["mfa_token"], "code": code}
        )
        assert response.status_code == 200, response.text
        data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture(scope="session")
def client():
    init_db()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def seeded(client):
    with session_scope() as db:
        users = {}
        for email, name, role in [
            ("teacher@test.demo", "Test Teacher", Role.TEACHER),
            ("student@test.demo", "Test Student", Role.STUDENT),
            ("student2@test.demo", "Second Student", Role.STUDENT),
            ("hod@test.demo", "Test HOD", Role.HOD),
        ]:
            user = User(
                email=email,
                full_name=name,
                role=role,
                department="Computer Science",
                password_hash=hash_password(PASSWORD),
            )
            if role == Role.HOD:
                user.mfa_enabled = True
                user.mfa_secret_enc = seal_secret(new_totp_secret())
            db.add(user)
            users[email] = user
        db.flush()

        subject = Subject(
            code="CS901",
            name="Secure Systems",
            description="Test subject",
            department="Computer Science",
            created_by=users["hod@test.demo"].id,
        )
        db.add(subject)
        db.flush()
        topic_names = ["Normalization", "Transactions", "Scheduling", "Memory"]
        topics = []
        for index, name in enumerate(topic_names, start=1):
            topic = Topic(subject_id=subject.id, name=name, unit=f"Unit {index}", description=name)
            db.add(topic)
            topics.append(topic)
        academic_service.assign_teacher(db, subject.id, users["teacher@test.demo"].id)
        academic_service.enroll_students(
            db, subject.id, [users["student@test.demo"].id, users["student2@test.demo"].id]
        )
        db.commit()
        subject_id = subject.id
        topic_ids = [topic.id for topic in topics]
        teacher_id = users["teacher@test.demo"].id
        student_id = users["student@test.demo"].id
        student2_id = users["student2@test.demo"].id

    with session_scope() as db:
        teacher = db.get(User, teacher_id)
        syllabus_service.upload(
            db,
            actor=teacher,
            subject_id=subject_id,
            filename="revision-notes.txt",
            content_type="text/plain",
            data=SYLLABUS_TEXT.encode(),
            ip="127.0.0.1",
        )

    question_specs = [
        ("Normalization", "c", "Third normal form removes transitive dependencies."),
        ("Transactions", "a", "Acyclic precedence graphs imply conflict serializability."),
        ("Scheduling", "b", "Priority scheduling can starve low-priority work."),
        ("Memory", "b", "A page fault means the page is not resident."),
    ]
    with session_scope() as db:
        teacher = db.get(User, teacher_id)
        questions = []
        for index, (topic_name, answer, explanation) in enumerate(question_specs):
            questions.append(
                PracticeQuestionCreate(
                    topic_id=topic_ids[index],
                    question_text=f"Test question {index + 1} about {topic_name}?",
                    question_type=QuestionType.SINGLE_CHOICE,
                    options=[
                        OptionIn(id="a", text="First option"),
                        OptionIn(id="b", text="Second option"),
                        OptionIn(id="c", text="Third option"),
                        OptionIn(id="d", text="Fourth option"),
                    ],
                    correct_answers=[answer],
                    explanation=explanation,
                    marks=1,
                    difficulty=Difficulty.MEDIUM,
                    bloom_level=BloomLevel.UNDERSTAND,
                )
            )
        payload = PracticeTestCreate(
            subject_id=subject_id,
            title="Seeded Practice Test",
            description="Fixture test",
            duration_minutes=20,
            difficulty=Difficulty.MEDIUM,
            topic_ids=topic_ids,
            questions=questions,
        )
        test_id = practice_service.create_test(db, teacher, payload)["test_id"]
        db.flush()
        practice_service.set_status(db, teacher, test_id, "PUBLISHED")
        db.commit()

    return {
        "subject_id": subject_id,
        "topic_ids": topic_ids,
        "teacher_id": teacher_id,
        "student_id": student_id,
        "student2_id": student2_id,
        "test_id": test_id,
    }


@pytest.fixture(scope="session")
def tokens(client, seeded):
    return {
        "teacher": _token_headers(client, "teacher@test.demo"),
        "student": _token_headers(client, "student@test.demo"),
        "student2": _token_headers(client, "student2@test.demo"),
        "hod": _token_headers(client, "hod@test.demo"),
    }


def exam_payload(subject_id: str, topic_ids: list[str], *, date_offset_minutes: int = 60) -> dict:
    from datetime import datetime, timedelta, timezone

    exam_date = datetime.now(timezone.utc) + timedelta(minutes=date_offset_minutes)
    return ExamConfigCreate(
        subject_id=subject_id,
        title="Secure Systems Internal Assessment",
        exam_type="Internal Assessment 1",
        exam_date=exam_date,
        duration_minutes=60,
        venue="Test Hall",
        instructions="Answer all questions.",
        total_marks=20,
        pattern=[
            PatternItem(marks=5, count=4, question_type=QuestionType.SHORT_ANSWER, label="Section A")
        ],
        topics=[ExamTopicIn(topic_id=topic_id, weight_percent=25) for topic_id in topic_ids],
        difficulty_distribution={
            Difficulty.EASY: 25,
            Difficulty.MEDIUM: 50,
            Difficulty.HARD: 25,
        },
        bloom_distribution={
            BloomLevel.REMEMBER: 25,
            BloomLevel.UNDERSTAND: 25,
            BloomLevel.APPLY: 25,
            BloomLevel.ANALYZE: 25,
        },
        retry_limit=3,
    ).model_dump(mode="json")


@pytest.fixture(scope="session")
def secured_paper(client, tokens, seeded):
    response = client.post(
        "/api/exams", headers=tokens["teacher"], json=exam_payload(seeded["subject_id"], seeded["topic_ids"])
    )
    assert response.status_code == 201, response.text
    exam = response.json()
    response = client.post(f"/api/exams/{exam['id']}/submit", headers=tokens["teacher"])
    assert response.status_code == 200, response.text
    response = client.post(f"/api/exams/{exam['id']}/generate", headers=tokens["teacher"])
    assert response.status_code == 200, response.text
    request_id = response.json()["request_id"]

    from app.services.generation_service import run_generation

    run_generation(request_id)

    with session_scope() as db:
        record = db.get(InternalExam, exam["id"])
        assert record is not None
        assert record.status == ExamStatus.SECURED, client.get(
            f"/api/exams/{exam['id']}/generation", headers=tokens["teacher"]
        ).json()
        config = db.execute(
            __import__("sqlalchemy").select(ExamConfiguration).where(
                ExamConfiguration.exam_id == exam["id"]
            )
        ).scalar_one()
        assert config.frozen is True
    return {"exam_id": exam["id"], "request_id": request_id}
