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
    DISCARDED = "DISCARDED"


# A paper that never reached secure storage, or one that was superseded or
# cancelled before release, is DISCARDED. Discarded papers are terminal: a retry
# or replacement always creates a new paper version.
PAPER_TRANSITIONS: dict[PaperState, set[PaperState]] = {
    PaperState.DRAFT: {PaperState.SUBMITTED},
    # SUBMITTED -> LOCKED is the uploaded-paper path: a teacher-prepared PDF
    # skips generation and validation and is locked as received.
    PaperState.SUBMITTED: {PaperState.GENERATING, PaperState.LOCKED, PaperState.DISCARDED},
    PaperState.GENERATING: {PaperState.VALIDATING, PaperState.SUBMITTED, PaperState.DISCARDED},
    PaperState.VALIDATING: {PaperState.ASSEMBLED, PaperState.GENERATING, PaperState.DISCARDED},
    PaperState.ASSEMBLED: {PaperState.VALIDATED, PaperState.GENERATING, PaperState.DISCARDED},
    PaperState.VALIDATED: {PaperState.LOCKED, PaperState.DISCARDED},
    PaperState.LOCKED: {PaperState.ENCRYPTED, PaperState.DISCARDED},
    PaperState.ENCRYPTED: {PaperState.STORED, PaperState.RELEASE_PENDING, PaperState.DISCARDED},
    PaperState.STORED: {PaperState.RELEASE_PENDING, PaperState.DISCARDED},
    PaperState.RELEASE_PENDING: {PaperState.DECRYPTED, PaperState.DISCARDED},
    PaperState.DECRYPTED: {PaperState.PRINTED},
    PaperState.PRINTED: {PaperState.RELEASED},
    PaperState.RELEASED: set(),
    PaperState.DISCARDED: set(),
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
    CANCELLED = "CANCELLED"


class ExamSource(StrEnum):
    GENERATED = "GENERATED"
    UPLOADED = "UPLOADED"


PRINT_LIMIT_PER_PAPER = 3


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
