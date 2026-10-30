"""Role-aware analytics dashboards."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.analytics import engine
from app.core.deps import Principal, get_principal
from app.core.errors import Forbidden
from app.db.session import get_db
from app.models.enums import Role

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard")
def dashboard(
    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> dict[str, Any]:
    role = principal.user.role
    if role == Role.STUDENT:
        return engine.student_dashboard(db, principal.user)
    if role == Role.TEACHER:
        return engine.teacher_dashboard(db, principal.user)
    if role == Role.HOD:
        return engine.hod_dashboard(db, principal.user)
    raise Forbidden("Unknown role.")


@router.get("/student")
def student_dashboard(
    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> dict[str, Any]:
    if principal.user.role == Role.TEACHER:
        raise Forbidden("Use the teacher dashboard.")
    if principal.user.role == Role.HOD:
        return engine.hod_dashboard(db, principal.user)
    return engine.student_dashboard(db, principal.user)


@router.get("/student/{student_id}")
def student_analytics(
    student_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if principal.user.role == Role.STUDENT and principal.user.id != student_id:
        raise Forbidden("You can only view your own analytics.")
    if principal.user.role == Role.TEACHER:
        raise Forbidden(
            "Open the analytics for one of your practice tests instead.",
            code="USE_TEST_ANALYTICS",
        )
    from app.core.errors import NotFound
    from app.models.tables import User

    student = db.get(User, student_id)
    if student is None or student.role != Role.STUDENT:
        raise NotFound("Student")
    result = engine.student_analytics(db, student_id)
    result["student"] = {"id": student.id, "full_name": student.full_name, "email": student.email}
    return result
