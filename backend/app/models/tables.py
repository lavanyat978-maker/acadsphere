"""SQLAlchemy models (normalized schema).

Security notes baked into the schema:
* `question_pool.question_text` is nulled ("sealed") once the final paper is
  encrypted, so post-encryption plaintext for selected questions does not stay
  at rest in the operational database.
* `encrypted_papers` stores only wrapped data keys, nonces, hashes and a pointer
  to the ciphertext blob; the plaintext paper is never persisted.
* `audit_logs` is an append-only hash chain (`prev_hash` -> `record_hash`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import (
    AttemptStatus,
    BloomLevel,
    Difficulty,
    ExamSource,
    ExamStatus,
    GenerationStatus,
    PaperState,
    PracticeTestStatus,
    QuestionType,
    ReleaseStatus,
    Role,
    Severity,
    ValidationStatus,
)

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str] = mapped_column(String(120), default="Computer Science")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    teacher_subjects: Mapped[list["SubjectTeacher"]] = relationship(
        back_populates="teacher", cascade="all, delete-orphan"
    )


class Subject(Base, TimestampMixin):
    __tablename__ = "subjects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    department: Mapped[str] = mapped_column(String(120), default="Computer Science")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    topics: Mapped[list["Topic"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan", order_by="Topic.name"
    )
    teachers: Mapped[list["SubjectTeacher"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )


class SubjectTeacher(Base):
    __tablename__ = "subject_teachers"
    __table_args__ = (UniqueConstraint("subject_id", "teacher_id", name="uq_subject_teacher"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    subject: Mapped[Subject] = relationship(back_populates="teachers")
    teacher: Mapped[User] = relationship(back_populates="teacher_subjects")


class StudentSubject(Base):
    """Enrolment: a student sees practice tests only for subjects they are enrolled in."""

    __tablename__ = "student_subjects"
    __table_args__ = (UniqueConstraint("subject_id", "student_id", name="uq_subject_student"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Topic(Base, TimestampMixin):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("subject_id", "name", name="uq_topic_subject_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(80), default="Unit 1")
    description: Mapped[str] = mapped_column(Text, default="")

    subject: Mapped[Subject] = relationship(back_populates="topics")


class SyllabusDocument(Base, TimestampMixin):
    __tablename__ = "syllabus_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PROCESSED")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("syllabus_documents.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[SyllabusDocument] = relationship(back_populates="chunks")


class PracticeTest(Base, TimestampMixin):
    __tablename__ = "practice_tests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    difficulty: Mapped[str] = mapped_column(String(10), default=Difficulty.MEDIUM)
    total_marks: Mapped[int] = mapped_column(Integer, default=0)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=PracticeTestStatus.DRAFT, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subject: Mapped[Subject] = relationship()
    topics: Mapped[list["PracticeTestTopic"]] = relationship(
        back_populates="test", cascade="all, delete-orphan"
    )
    questions: Mapped[list["PracticeQuestion"]] = relationship(
        back_populates="test", cascade="all, delete-orphan", order_by="PracticeQuestion.position"
    )


class PracticeTestTopic(Base):
    __tablename__ = "practice_test_topics"
    __table_args__ = (UniqueConstraint("test_id", "topic_id", name="uq_practice_test_topic"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_id: Mapped[str] = mapped_column(ForeignKey("practice_tests.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)

    test: Mapped[PracticeTest] = relationship(back_populates="topics")
    topic: Mapped[Topic] = relationship()


class PracticeQuestion(Base):
    __tablename__ = "practice_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_id: Mapped[str] = mapped_column(ForeignKey("practice_tests.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"), index=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), default=QuestionType.SINGLE_CHOICE)
    options: Mapped[list[dict]] = mapped_column(JSON, default=list)
    correct_answers: Mapped[list[str]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    marks: Mapped[int] = mapped_column(Integer, default=1)
    difficulty: Mapped[str] = mapped_column(String(10), default=Difficulty.MEDIUM)
    bloom_level: Mapped[str] = mapped_column(String(20), default=BloomLevel.UNDERSTAND)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    test: Mapped[PracticeTest] = relationship(back_populates="questions")
    topic: Mapped[Topic] = relationship()


class PracticeAttempt(Base, TimestampMixin):
    __tablename__ = "practice_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_id: Mapped[str] = mapped_column(ForeignKey("practice_tests.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=AttemptStatus.IN_PROGRESS, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    max_score: Mapped[float] = mapped_column(Float, default=0)
    accuracy: Mapped[float] = mapped_column(Float, default=0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    auto_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Autosaved selections ({question_id: {"selected": [...], "response_time_seconds": n}}).
    # Graded at submission, and at the deadline if the student never submits.
    draft_answers: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    last_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    test: Mapped[PracticeTest] = relationship()
    answers: Mapped[list["PracticeAnswer"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class PracticeAnswer(Base):
    __tablename__ = "practice_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_answer_attempt_question"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("practice_attempts.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(ForeignKey("practice_questions.id"), index=True)
    selected: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    awarded_marks: Mapped[float] = mapped_column(Float, default=0)
    response_time_seconds: Mapped[float] = mapped_column(Float, default=0)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    attempt: Mapped[PracticeAttempt] = relationship(back_populates="answers")
    question: Mapped[PracticeQuestion] = relationship()


class PracticeResult(Base):
    __tablename__ = "practice_results"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_result_attempt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("practice_attempts.id", ondelete="CASCADE"), index=True)
    test_id: Mapped[str] = mapped_column(ForeignKey("practice_tests.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    max_score: Mapped[float] = mapped_column(Float, default=0)
    percentage: Mapped[float] = mapped_column(Float, default=0)
    accuracy: Mapped[float] = mapped_column(Float, default=0)
    avg_response_time: Mapped[float] = mapped_column(Float, default=0)
    topic_breakdown: Mapped[list[dict]] = mapped_column(JSON, default=list)
    difficulty_breakdown: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InternalExam(Base, TimestampMixin):
    __tablename__ = "internal_exams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    exam_type: Mapped[str] = mapped_column(String(80), default="Internal Assessment 1")
    exam_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=90)
    total_marks: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(30), default=ExamStatus.DRAFT, index=True)
    instructions: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[str] = mapped_column(String(160), default="Examination Hall A")
    source: Mapped[str] = mapped_column(String(20), default=ExamSource.GENERATED)

    subject: Mapped[Subject] = relationship()
    topics: Mapped[list["ExamTopic"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan"
    )
    configuration: Mapped["ExamConfiguration | None"] = relationship(
        back_populates="exam", uselist=False, cascade="all, delete-orphan"
    )
    generation_requests: Mapped[list["GenerationRequest"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", order_by="GenerationRequest.created_at"
    )
    papers: Mapped[list["QuestionPaper"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan"
    )


class ExamTopic(Base):
    __tablename__ = "exam_topics"
    __table_args__ = (UniqueConstraint("exam_id", "topic_id", name="uq_exam_topic"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    exam_id: Mapped[str] = mapped_column(ForeignKey("internal_exams.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"), index=True)
    weight_percent: Mapped[float] = mapped_column(Float, default=0)

    exam: Mapped[InternalExam] = relationship(back_populates="topics")
    topic: Mapped[Topic] = relationship()


class ExamConfiguration(Base):
    __tablename__ = "exam_configurations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("internal_exams.id", ondelete="CASCADE"), unique=True, index=True
    )
    total_marks: Mapped[int] = mapped_column(Integer, nullable=False)
    pattern: Mapped[list[dict]] = mapped_column(JSON, default=list)
    difficulty_distribution: Mapped[dict] = mapped_column(JSON, default=dict)
    bloom_distribution: Mapped[dict] = mapped_column(JSON, default=dict)
    topic_distribution: Mapped[dict] = mapped_column(JSON, default=dict)
    instructions: Mapped[str] = mapped_column(Text, default="")
    retry_limit: Mapped[int] = mapped_column(Integer, default=3)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    frozen_hash: Mapped[str] = mapped_column(String(64), default="")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    exam: Mapped[InternalExam] = relationship(back_populates="configuration")


class GenerationRequest(Base, TimestampMixin):
    __tablename__ = "generation_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    exam_id: Mapped[str] = mapped_column(ForeignKey("internal_exams.id", ondelete="CASCADE"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=GenerationStatus.QUEUED, index=True)
    provider: Mapped[str] = mapped_column(String(40), default="mock")
    mode: Mapped[str] = mapped_column(String(20), default="DEMO")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    candidates_generated: Mapped[int] = mapped_column(Integer, default=0)
    candidates_rejected: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    exam: Mapped[InternalExam] = relationship(back_populates="generation_requests")


class QuestionPool(Base):
    __tablename__ = "question_pool"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    exam_id: Mapped[str] = mapped_column(ForeignKey("internal_exams.id", ondelete="CASCADE"), index=True)
    generation_request_id: Mapped[str] = mapped_column(
        ForeignKey("generation_requests.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id"), index=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"), index=True)
    unit: Mapped[str] = mapped_column(String(80), default="")
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_type: Mapped[str] = mapped_column(String(30), default=QuestionType.SHORT_ANSWER)
    marks: Mapped[int] = mapped_column(Integer, default=2)
    difficulty: Mapped[str] = mapped_column(String(10), default=Difficulty.MEDIUM)
    bloom_level: Mapped[str] = mapped_column(String(20), default=BloomLevel.UNDERSTAND)
    expected_answer: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(255), default="")
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    validation_status: Mapped[str] = mapped_column(
        String(20), default=ValidationStatus.PENDING, index=True
    )
    generation_attempt: Mapped[int] = mapped_column(Integer, default=1)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sealed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    topic: Mapped[Topic] = relationship()


class QuestionMetadata(Base):
    __tablename__ = "question_metadata"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question_id: Mapped[str] = mapped_column(
        ForeignKey("question_pool.id", ondelete="CASCADE"), unique=True, index=True
    )
    generation_model: Mapped[str] = mapped_column(String(80), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="v1")
    references: Mapped[list[dict]] = mapped_column(JSON, default=list)
    raw_response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question_id: Mapped[str | None] = mapped_column(
        ForeignKey("question_pool.id", ondelete="CASCADE"), nullable=True, index=True
    )
    exam_id: Mapped[str | None] = mapped_column(
        ForeignKey("internal_exams.id", ondelete="CASCADE"), nullable=True, index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("question_papers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    level: Mapped[int] = mapped_column(Integer, default=1)
    validator: Mapped[str] = mapped_column(String(60), default="rules")
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(Float, default=0)
    issues: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QuestionPaper(Base):
    __tablename__ = "question_papers"
    __table_args__ = (UniqueConstraint("exam_id", "version", name="uq_paper_exam_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    exam_id: Mapped[str] = mapped_column(ForeignKey("internal_exams.id", ondelete="CASCADE"), index=True)
    generation_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_requests.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(30), default=PaperState.DRAFT, index=True)
    total_marks: Mapped[int] = mapped_column(Integer, default=0)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    structure: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    plaintext_sha256: Mapped[str] = mapped_column(String(64), default="")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    encrypted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    exam: Mapped[InternalExam] = relationship(back_populates="papers")


class EncryptedPaper(Base):
    __tablename__ = "encrypted_papers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("question_papers.id", ondelete="CASCADE"), unique=True, index=True
    )
    exam_id: Mapped[str] = mapped_column(ForeignKey("internal_exams.id", ondelete="CASCADE"), index=True)
    algorithm: Mapped[str] = mapped_column(String(40), default="AES-256-GCM")
    key_id: Mapped[str] = mapped_column(String(60), nullable=False)
    wrapped_dek: Mapped[str] = mapped_column(Text, nullable=False)
    payload_nonce: Mapped[str] = mapped_column(String(60), nullable=False)
    ciphertext_path: Mapped[str] = mapped_column(String(500), nullable=False)
    ciphertext_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plaintext_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext_bytes: Mapped[int] = mapped_column(Integer, default=0)
    aad: Mapped[str] = mapped_column(String(255), default="")
    # application/json for generated papers, application/pdf for uploaded ones.
    content_type: Mapped[str] = mapped_column(String(80), default="application/json")
    original_name: Mapped[str] = mapped_column(String(255), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperStateTransition(Base):
    __tablename__ = "paper_state_transitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("question_papers.id", ondelete="CASCADE"), index=True)
    from_state: Mapped[str] = mapped_column(String(30), nullable=False)
    to_state: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_role: Mapped[str] = mapped_column(String(20), default="SYSTEM")
    reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperRelease(Base):
    __tablename__ = "paper_releases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("question_papers.id", ondelete="CASCADE"), index=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("internal_exams.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=ReleaseStatus.AUTHORIZED, index=True)
    release_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decrypted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    print_count: Mapped[int] = mapped_column(Integer, default=0)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, default=_uuid)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_email: Mapped[str] = mapped_column(String(255), default="")
    actor_role: Mapped[str] = mapped_column(String(20), default="ANONYMOUS")
    event: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(60), default="")
    resource_id: Mapped[str] = mapped_column(String(60), default="")
    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), default=Severity.INFO, index=True)
    summary: Mapped[str] = mapped_column(String(255), default="")
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
