from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import BloomLevel, Difficulty, ExamSource, QuestionType


class PatternItem(BaseModel):
    marks: int = Field(ge=1, le=50)
    count: int = Field(ge=1, le=30)
    question_type: QuestionType = QuestionType.SHORT_ANSWER
    label: str = Field(default="", max_length=80)


class ExamTopicIn(BaseModel):
    topic_id: str
    weight_percent: float = Field(ge=0, le=100)


class ExamConfigCreate(BaseModel):
    """Blueprint for an internal exam.

    GENERATED exams need a full blueprint (pattern, topic weights, difficulty and
    Bloom mix) because the paper is assembled to it. UPLOADED exams carry a
    teacher-prepared PDF, so only the logistics and total marks are required;
    topics are optional metadata.
    """

    source: ExamSource = ExamSource.GENERATED
    subject_id: str
    title: str = Field(min_length=3, max_length=200)
    exam_type: str = Field(default="Internal Assessment 1", max_length=80)
    exam_date: datetime
    duration_minutes: int = Field(default=90, ge=15, le=300)
    venue: str = Field(default="Examination Hall A", max_length=160)
    instructions: str = Field(default="", max_length=3000)
    total_marks: int = Field(ge=5, le=200)
    pattern: list[PatternItem] = Field(default_factory=list, max_length=10)
    topics: list[ExamTopicIn] = Field(default_factory=list, max_length=20)
    difficulty_distribution: dict[Difficulty, float] = Field(default_factory=dict)
    bloom_distribution: dict[BloomLevel, float] = Field(default_factory=dict)
    retry_limit: int = Field(default=3, ge=1, le=6)

    @model_validator(mode="after")
    def validate_paper_shape(self) -> "ExamConfigCreate":
        topic_ids = [topic.topic_id for topic in self.topics]
        if len(set(topic_ids)) != len(topic_ids):
            raise ValueError("Each topic may appear only once")
        if self.source == ExamSource.UPLOADED:
            if self.pattern:
                computed = sum(item.marks * item.count for item in self.pattern)
                if computed != self.total_marks:
                    raise ValueError(
                        f"Question pattern totals {computed} marks but total_marks is {self.total_marks}"
                    )
            return self
        if not self.pattern:
            raise ValueError("A generated paper needs a question pattern")
        if not self.topics:
            raise ValueError("Select at least one topic for a generated paper")
        if not self.difficulty_distribution or not self.bloom_distribution:
            raise ValueError("A generated paper needs difficulty and Bloom distributions")
        computed = sum(item.marks * item.count for item in self.pattern)
        if computed != self.total_marks:
            raise ValueError(
                f"Question pattern totals {computed} marks but total_marks is {self.total_marks}"
            )
        for label, weights in (
            ("topic", {topic.topic_id: topic.weight_percent for topic in self.topics}),
            ("difficulty", self.difficulty_distribution),
            ("Bloom", self.bloom_distribution),
        ):
            total = sum(float(value) for value in weights.values())
            if abs(total - 100.0) > 0.51:
                raise ValueError(f"{label} distribution must sum to 100 (got {total:.2f})")
            if any(float(value) < 0 for value in weights.values()):
                raise ValueError(f"{label} distribution cannot contain negative values")
        if sum(item.count for item in self.pattern) > 30:
            raise ValueError("A single paper may not exceed 30 questions")
        total_slots = sum(item.count for item in self.pattern)
        if total_slots < len(self.topics):
            raise ValueError("Provide at least one question slot per selected topic")
        return self

    @field_validator("exam_date")
    @classmethod
    def require_future(cls, value: datetime) -> datetime:
        from datetime import timezone

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        if value.date() < datetime.now(timezone.utc).date():
            raise ValueError("Examination date cannot be in the past")
        return value


class ExamConfigOut(BaseModel):
    total_marks: int
    pattern: list[dict]
    difficulty_distribution: dict
    bloom_distribution: dict
    topic_distribution: dict
    instructions: str
    retry_limit: int
    frozen: bool
    frozen_at: datetime | None
    frozen_hash: str


class ExamOut(BaseModel):
    id: str
    source: str = ExamSource.GENERATED
    can_edit: bool = False
    created_by_id: str = ""
    paper_version: int | None = None
    paper_file_name: str = ""
    paper_page_count: int = 0
    replaceable_until: datetime | None = None
    window_opens_at: datetime | None = None
    window_closes_at: datetime | None = None
    subject_id: str
    subject_name: str
    title: str
    exam_type: str
    exam_date: datetime
    duration_minutes: int
    total_marks: int
    venue: str
    instructions: str
    status: str
    created_by_name: str
    topics: list[dict]
    configuration: ExamConfigOut | None = None
    generation_status: str | None = None
    generation_request_id: str | None = None
    paper_state: str | None = None
    paper_id: str | None = None
    created_at: datetime


class GenerationStatusOut(BaseModel):
    request_id: str
    exam_id: str
    status: str
    mode: str
    provider: str
    attempts: int
    candidates_generated: int
    candidates_rejected: int
    error: str | None = None
    stage: str
    progress: int
    paper_state: str | None = None
    validation: dict
    started_at: datetime | None = None
    completed_at: datetime | None = None
    secure_summary: str


class SecurePaperOut(BaseModel):
    paper_id: str
    exam_id: str
    version: int
    state: str
    total_marks: int
    question_count: int
    algorithm: str
    key_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    content_type: str = ""
    original_name: str = ""
    page_count: int = 0
    encrypted_at: datetime | None
    locked_at: datetime | None
    plaintext_hash_matches: bool
    transitions: list[dict] = []


class ReleaseQueueItem(BaseModel):
    paper_id: str
    exam_id: str
    source: str = ExamSource.GENERATED
    paper_version: int = 1
    page_count: int = 0
    prints_used: int = 0
    prints_remaining: int = 3
    exam_title: str
    subject_name: str
    exam_type: str
    exam_date: datetime
    duration_minutes: int
    total_marks: int
    venue: str
    paper_state: str
    encrypted_at: datetime | None
    window_opens_at: datetime
    window_closes_at: datetime
    window_open: bool
    release: dict | None = None


class AuthorizeReleaseRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=255)


class AuthzReleaseOut(BaseModel):
    paper_id: str
    release_token: str
    expires_at: datetime
    exam_date: datetime
    window_closes_at: datetime
    message: str


class DecryptRequest(BaseModel):
    release_token: str = Field(min_length=20, max_length=200)
    reason: str = Field(min_length=5, max_length=255)


class DecryptOut(BaseModel):
    paper_id: str
    decrypted: bool
    integrity_verified: bool
    question_count: int
    page_count: int = 0
    content_type: str = ""
    sections: list[dict]
    plaintext_ttl_seconds: int
    message: str


class PrintOut(BaseModel):
    paper_id: str
    printed: bool
    print_count: int
    prints_remaining: int
    message: str


class CancelExamRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=255)


class ReleaseHistoryItem(BaseModel):
    release_id: str
    paper_id: str
    exam_title: str
    subject_name: str
    status: str
    authorized_by_name: str
    authorized_at: datetime
    decrypted_at: datetime | None
    printed_at: datetime | None
    print_count: int
    reason: str


class AuditLogOut(BaseModel):
    seq: int
    event: str
    actor_email: str
    actor_role: str
    resource_type: str
    resource_id: str
    success: bool
    ip_address: str
    created_at: datetime
    metadata_json: dict
    record_hash: str
