"""Subjects, topics and teacher assignment endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_principal, require_hod, require_staff
from app.db.session import get_db
from app.models.tables import User
from app.schemas.academic import SubjectCreate, SubjectOut, TopicCreate, TopicOut
from app.services import academic_service
from app.services.audit_service import record_audit

router = APIRouter(prefix="/subjects", tags=["academic"])


@router.get("", response_model=list[SubjectOut])
def list_subjects(
    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> list[dict]:
    return academic_service.list_subjects(db, principal.user)


@router.post("", response_model=SubjectOut, status_code=201)
def create_subject(
    payload: SubjectCreate,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict:
    subject = academic_service.create_subject(db, payload, principal.user)
    record_audit(
        db,
        event="SUBJECT_CREATED",
        actor=principal.user,
        resource_type="subject",
        resource_id=subject.id,
        ip=principal.ip,
        metadata={"code": subject.code, "name": subject.name},
    )
    db.commit()
    rows = academic_service.list_subjects(db, principal.user)
    return next(row for row in rows if row["id"] == subject.id)


@router.get("/{subject_id}/topics", response_model=list[TopicOut])
def list_topics(
    subject_id: str,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list[dict]:
    academic_service.ensure_subject_access(db, principal.user, subject_id)
    return [
        {"id": topic.id, "name": topic.name, "unit": topic.unit, "description": topic.description}
        for topic in academic_service.topics_for_subject(db, subject_id)
    ]


@router.post("/{subject_id}/topics", response_model=TopicOut, status_code=201)
def create_topic(
    subject_id: str,
    payload: TopicCreate,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    topic = academic_service.create_topic(db, subject_id, payload, principal.user)
    record_audit(
        db,
        event="TOPIC_CREATED",
        actor=principal.user,
        resource_type="topic",
        resource_id=topic.id,
        ip=principal.ip,
        metadata={"subject_id": subject_id, "name": topic.name},
    )
    db.commit()
    return {"id": topic.id, "name": topic.name, "unit": topic.unit, "description": topic.description}


@router.post("/{subject_id}/teachers/{teacher_id}", status_code=201)
def assign_teacher(
    subject_id: str,
    teacher_id: str,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    subject = academic_service.ensure_subject_access(db, principal.user, subject_id)
    academic_service.assign_teacher(db, subject.id, teacher_id)
    record_audit(
        db,
        event="TEACHER_ASSIGNED_TO_SUBJECT",
        actor=principal.user,
        resource_type="subject",
        resource_id=subject.id,
        ip=principal.ip,
        metadata={"teacher_id": teacher_id},
    )
    db.commit()
    return {"status": "assigned", "subject_id": subject.id, "teacher_id": teacher_id}


@router.get("/teachers/all", response_model=list[dict])
def list_teachers(
    principal: Principal = Depends(require_hod), db: Session = Depends(get_db)
) -> list[dict]:
    from sqlalchemy import select

    from app.models.enums import Role

    teachers: list[User] = list(
        db.execute(select(User).where(User.role == Role.TEACHER).order_by(User.full_name))
        .scalars()
        .all()
    )
    assignments = {
        teacher_id: academic_service.teacher_subject_ids(db, teacher_id) for teacher_id in [t.id for t in teachers]
    }
    return [
        {
            "id": teacher.id,
            "email": teacher.email,
            "full_name": teacher.full_name,
            "department": teacher.department,
            "mfa_enabled": teacher.mfa_enabled,
            "subject_ids": sorted(assignments.get(teacher.id, set())),
        }
        for teacher in teachers
    ]
