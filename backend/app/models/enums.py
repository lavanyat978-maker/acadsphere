"""Domain enums. Stored as strings for portability across PostgreSQL/SQLite."""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    STUDENT = "STUDENT"
    TEACHER = "TEACHER"
    HOD = "HOD"


class Difficulty(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class BloomLevel(StrEnum):
    REMEMBER = "REMEMBER"
    UNDERSTAND = "UNDERSTAND"
    APPLY = "APPLY"
    ANALYZE = "ANALYZE"
    EVALUATE = "EVALUATE"
    CREATE = "CREATE"


class QuestionType(StrEnum):
    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTI_CHOICE = "MULTI_CHOICE"
    TRUE_FALSE = "TRUE_FALSE"
    SHORT_ANSWER = "SHORT_ANSWER"
    LONG_ANSWER = "LONG_ANSWER"


class PaperState(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    GENERATING = "GENERATING"
    VALIDATING = "VALIDATING"
    ASSEMBLED = "ASSEMBLED"
    VALIDATED = "VALIDATED"
    LOCKED = "LOCKED"
    ENCRYPTED = "ENCRYPTED"
    STORED = "STORED"
    RELEASE_PENDING = "RELEASE_PENDING"
    DECRYPTED = "DECRYPTED"
    PRINTED = "PRINTED"
    RELEASED = "RELEASED"


PAPER_TRANSITIONS: dict[PaperState, set[PaperState]] = {
    PaperState.DRAFT: {PaperState.SUBMITTED},
    PaperState.SUBMITTED: {PaperState.GENERATING},
    PaperState.GENERATING: {PaperState.VALIDATING, PaperState.SUBMITTED},
    PaperState.VALIDATING: {PaperState.ASSEMBLED, PaperState.GENERATING},
    PaperState.ASSEMBLED: {PaperState.VALIDATED, PaperState.GENERATING},
    PaperState.VALIDATED: {PaperState.LOCKED},
    PaperState.LOCKED: {PaperState.ENCRYPTED},
    PaperState.ENCRYPTED: {PaperState.STORED, PaperState.RELEASE_PENDING},
    PaperState.STORED: {PaperState.RELEASE_PENDING},
    PaperState.RELEASE_PENDING: {PaperState.DECRYPTED},
    PaperState.DECRYPTED: {PaperState.PRINTED},
    PaperState.PRINTED: {PaperState.RELEASED},
    PaperState.RELEASED: set(),
}


class ValidationStatus(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    DISCARDED = "DISCARDED"


class GenerationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PracticeTestStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class AttemptStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    AUTO_SUBMITTED = "AUTO_SUBMITTED"
    EXPIRED = "EXPIRED"


class ExamStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PROCESSING = "PROCESSING"
    SECURED = "SECURED"
    FAILED = "FAILED"
    RELEASED = "RELEASED"


class ReleaseStatus(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    DECRYPTED = "DECRYPTED"
    PRINTED = "PRINTED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
