from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import BloomLevel, Difficulty, QuestionType


class OptionIn(BaseModel):
    id: str = Field(min_length=1, max_length=4)
    text: str = Field(min_length=1, max_length=600)


class PracticeQuestionCreate(BaseModel):
    topic_id: str
    question_text: str = Field(min_length=5, max_length=2000)
    question_type: QuestionType = QuestionType.SINGLE_CHOICE
    options: list[OptionIn] = []
    correct_answers: list[str] = []
    explanation: str = Field(default="", max_length=2000)
    marks: int = Field(default=1, ge=1, le=10)
    difficulty: Difficulty = Difficulty.MEDIUM
    bloom_level: BloomLevel = BloomLevel.UNDERSTAND

    @model_validator(mode="after")
    def check_shape(self) -> "PracticeQuestionCreate":
        if self.question_type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE}:
            if len(self.options) < 2:
                raise ValueError("Choice questions need at least two options")
            option_ids = {opt.id for opt in self.options}
            if len(option_ids) != len(self.options):
                raise ValueError("Option identifiers must be unique")
            if not self.correct_answers:
                raise ValueError("At least one correct option is required")
            if not set(self.correct_answers).issubset(option_ids):
                raise ValueError("Correct answers must reference existing options")
            if self.question_type == QuestionType.SINGLE_CHOICE and len(self.correct_answers) != 1:
                raise ValueError("Single-choice questions accept exactly one answer")
            if self.question_type == QuestionType.MULTI_CHOICE and len(self.correct_answers) < 2:
                raise ValueError("Multiple-choice questions require at least two correct options")
        elif self.question_type == QuestionType.TRUE_FALSE:
            if len(self.options) != 2:
                self.options = [
                    OptionIn(id="true", text="True"),
                    OptionIn(id="false", text="False"),
                ]
            if len(self.correct_answers) != 1 or self.correct_answers[0] not in {"true", "false"}:
                raise ValueError("True/false questions need exactly one of 'true' or 'false'")
        return self


class PracticeTestCreate(BaseModel):
    subject_id: str
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=2000)
    duration_minutes: int = Field(default=30, ge=1, le=240)
    difficulty: Difficulty = Difficulty.MEDIUM
    topic_ids: list[str] = Field(min_length=1, max_length=20)
    questions: list[PracticeQuestionCreate] = Field(default_factory=list, max_length=60)


class PracticeTestUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    duration_minutes: int | None = Field(default=None, ge=1, le=240)


class GeneratePracticeQuestionsRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=20)
    difficulty: Difficulty | None = None
    topic_id: str | None = None
    replace_existing: bool = False


class PracticeTestOut(BaseModel):
    id: str
    subject_id: str
    subject_name: str
    title: str
    description: str
    duration_minutes: int
    difficulty: str
    question_count: int
    total_marks: int
    status: str
    topics: list[str] = []
    attempt_count: int = 0
    best_score: float | None = None
    last_attempt_at: datetime | None = None
    can_edit: bool = False
    created_by_name: str = ""


class PracticeQuestionOut(BaseModel):
    id: str
    question_text: str
    question_type: str
    options: list[dict]
    marks: int
    difficulty: str
    bloom_level: str
    topic_id: str
    topic_name: str = ""
    position: int
    explanation: str | None = None
    correct_answers: list[str] | None = None


class PracticeTestDetail(PracticeTestOut):
    questions: list[PracticeQuestionOut] = []


class AnswerSubmit(BaseModel):
    question_id: str
    selected: list[str] = Field(default_factory=list, max_length=10)
    response_time_seconds: float = Field(default=0, ge=0, le=36000)


class AttemptSubmit(BaseModel):
    answers: list[AnswerSubmit] = Field(default_factory=list, max_length=100)


class AttemptAutosave(BaseModel):
    answers: list[AnswerSubmit] = Field(default_factory=list, max_length=100)


class AutosaveOut(BaseModel):
    saved: int
    last_saved_at: datetime
    expires_at: datetime
    server_time: datetime


class AttemptOut(BaseModel):
    id: str
    test_id: str
    test_title: str
    duration_minutes: int
    status: str
    started_at: datetime
    expires_at: datetime
    server_time: datetime
    total_marks: int
    warning: str
    saved_answers: dict[str, dict] = {}
    last_saved_at: datetime | None = None


class AttemptDetail(AttemptOut):
    questions: list[PracticeQuestionOut] = []


class ReviewItem(BaseModel):
    question_id: str
    question_text: str
    question_type: str
    options: list[dict]
    selected: list[str]
    correct_answers: list[str]
    is_correct: bool
    awarded_marks: float
    marks: int
    explanation: str
    topic_name: str
    difficulty: str
    response_time_seconds: float


class ResultOut(BaseModel):
    id: str
    attempt_id: str
    test_id: str
    test_title: str
    subject_name: str
    score: float
    max_score: float
    percentage: float
    accuracy: float
    avg_response_time: float
    created_at: datetime
    topic_breakdown: list[dict]
    difficulty_breakdown: list[dict]
    review: list[ReviewItem] = []
    auto_submitted: bool = False
    student_name: str | None = None


class TopicPerformance(BaseModel):
    topic_id: str
    topic_name: str
    subject_name: str
    attempted: int
    correct: int
    accuracy: float
    avg_time: float
    strength: str


class StudentAnalytics(BaseModel):
    total_attempts: int
    average_percentage: float
    best_percentage: float
    overall_accuracy: float
    avg_response_time: float
    improvement: float
    weak_topics: list[TopicPerformance]
    topic_performance: list[TopicPerformance]
    difficulty_performance: list[dict]
    trend: list[dict]
    recommendations: list[dict]


class AttemptHistoryItem(BaseModel):
    attempt_id: str
    test_id: str
    test_title: str
    subject_name: str
    started_at: datetime
    submitted_at: datetime | None
    score: float
    max_score: float
    percentage: float
    accuracy: float
    status: str


class PracticeAnalyticsOut(BaseModel):
    test_id: str
    test_title: str
    attempts: int
    average_percentage: float
    average_accuracy: float
    completion_rate: float
    topic_breakdown: list[dict]
    student_rows: list[dict]


class PracticeTestStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def allowed(cls, value: str) -> str:
        if value not in {"DRAFT", "PUBLISHED", "ARCHIVED"}:
            raise ValueError("Invalid status")
        return value
