"""Practice test authoring, attempts, results and analytics."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import Principal, require_staff, require_student
from app.db.session import get_db
from app.schemas.practice import (
    AttemptAutosave,
    AttemptDetail,
    AutosaveOut,
    AttemptHistoryItem,
    AttemptOut,
    AttemptSubmit,
    GeneratePracticeQuestionsRequest,
    PracticeAnalyticsOut,
    PracticeQuestionCreate,
    PracticeQuestionOut,
    PracticeTestCreate,
    PracticeTestDetail,
    PracticeTestOut,
    PracticeTestStatusUpdate,
    PracticeTestUpdate,
    ResultOut,
    StudentAnalytics,
)
from app.services import practice_service

router = APIRouter(prefix="/practice", tags=["practice"])


@router.get("/tests", response_model=list[PracticeTestOut])
def list_tests(
    subject_id: str | None = Query(default=None),
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
) -> list[dict]:
    return practice_service.list_tests(db, principal.user, subject_id)


@router.get("/staff/tests", response_model=list[PracticeTestOut])
def list_staff_tests(
    subject_id: str | None = Query(default=None),
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> list[dict]:
    return practice_service.list_tests(db, principal.user, subject_id)


@router.post("/tests", response_model=PracticeTestDetail, status_code=201)
def create_test(
    payload: PracticeTestCreate,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.create_test(db, principal.user, payload)
    db.commit()
    return result["detail"]


@router.get("/tests/{test_id}", response_model=PracticeTestDetail)
def get_test(
    test_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
) -> dict:
    return practice_service.get_test(db, principal.user, test_id)


@router.get("/staff/tests/{test_id}", response_model=PracticeTestDetail)
def get_staff_test(
    test_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    return practice_service.get_test(db, principal.user, test_id, include_answers=True)


@router.patch("/tests/{test_id}", response_model=PracticeTestDetail)
def update_test(
    test_id: str,
    payload: PracticeTestUpdate,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.update_test(db, principal.user, test_id, payload)
    db.commit()
    return result


@router.post("/tests/{test_id}/status", response_model=PracticeTestDetail)
def set_status(
    test_id: str,
    payload: PracticeTestStatusUpdate,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.set_status(db, principal.user, test_id, payload.status)
    db.commit()
    return result


@router.post("/tests/{test_id}/questions", response_model=PracticeTestDetail, status_code=201)
def add_questions(
    test_id: str,
    payloads: list[PracticeQuestionCreate],
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.add_questions(db, principal.user, test_id, payloads)
    db.commit()
    return result


@router.put("/tests/{test_id}/questions/{question_id}", response_model=PracticeTestDetail)
def update_question(
    test_id: str,
    question_id: str,
    payload: PracticeQuestionCreate,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.update_question(db, principal.user, test_id, question_id, payload)
    db.commit()
    return result


@router.delete("/tests/{test_id}/questions/{question_id}", response_model=PracticeTestDetail)
def delete_question(
    test_id: str,
    question_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.delete_question(db, principal.user, test_id, question_id)
    db.commit()
    return result


@router.post("/tests/{test_id}/generate")
def generate_questions(
    test_id: str,
    payload: GeneratePracticeQuestionsRequest,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = practice_service.generate_questions(db, principal.user, test_id, payload)
    db.commit()
    return result


@router.get("/tests/{test_id}/analytics", response_model=PracticeAnalyticsOut)
def test_analytics(
    test_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.get_test_analytics(db, principal.user, test_id)
    db.commit()
    return result


@router.post(
    "/tests/{test_id}/attempts", response_model=AttemptDetail, status_code=status.HTTP_201_CREATED
)
def start_attempt(
    test_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.start_attempt(db, principal.user, test_id)
    db.commit()
    return result


@router.get("/attempts/{attempt_id}", response_model=AttemptDetail)
def get_attempt(
    attempt_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.get_attempt(db, principal.user, attempt_id)
    db.commit()
    return result


@router.put("/attempts/{attempt_id}/answers", response_model=AutosaveOut)
def autosave_attempt(
    attempt_id: str,
    payload: AttemptAutosave,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
) -> dict:
    answers = [answer.model_dump() for answer in payload.answers]
    try:
        result = practice_service.autosave_attempt(db, principal.user, attempt_id, answers)
    finally:
        # An expired attempt is finalized inside the call; keep that even when it raises.
        db.commit()
    return result


@router.post("/attempts/{attempt_id}/submit", response_model=ResultOut)
def submit_attempt(
    attempt_id: str,
    payload: AttemptSubmit,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
) -> dict:
    answers = [answer.model_dump() for answer in payload.answers]
    result = practice_service.submit_attempt(db, principal.user, attempt_id, answers)
    db.commit()
    return result


@router.get("/attempts/{attempt_id}/result", response_model=ResultOut)
def get_result(
    attempt_id: str,
    principal: Principal = Depends(require_student),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.get_result(db, principal.user, attempt_id)
    db.commit()
    return result


@router.get("/staff/attempts/{attempt_id}/result", response_model=ResultOut)
def get_staff_result(
    attempt_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    result = practice_service.get_result(db, principal.user, attempt_id)
    db.commit()
    return result


@router.get("/history", response_model=list[AttemptHistoryItem])
def history(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
) -> list[dict]:
    result = practice_service.student_history(db, principal.user)
    db.commit()
    return result


@router.get("/analytics/me", response_model=StudentAnalytics)
def my_analytics(
    principal: Principal = Depends(require_student), db: Session = Depends(get_db)
) -> dict:
    from app.analytics.engine import student_analytics

    practice_service.finalize_expired_attempts(db, principal.user.id)
    db.commit()
    return student_analytics(db, principal.user.id)


__all__ = ["router", "PracticeQuestionOut", "AttemptOut"]
