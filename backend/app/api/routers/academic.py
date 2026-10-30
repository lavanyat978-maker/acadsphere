"""Subjects, topics, teacher assignment and student enrolment endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal, get_principal, require_hod, require_staff
from app.db.session import get_db
from app.models.enums import Role
from app.models.tables import User
from app.schemas.academic import (
    EnrollRequest,
    SubjectCreate,
    SubjectOut,
    SubjectUpdate,
    TopicCreate,
    TopicOut,
    TopicUpdate,
)
from app.services import academic_service
from app.services.access import ensure_subject_staff, teacher_subject_ids
from app.services.audit_service import record_audit

router = APIRouter(prefix="/subjects", tags=["academic"])


def _audit(db: Session, principal: Principal, event: str, resource_type: str, resource_id: str, **metadata) -> None:
    record_audit(
        db,
        event=event,
        actor=principal.user,
        resource_type=resource_type,
        resource_id=resource_id,
        ip=principal.ip,
        user_agent=principal.user_agent,
        metadata=metadata,
    )


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
    _audit(db, principal, "SUBJECT_CREATED", "subject", subject.id, code=subject.code, name=subject.name)
    db.commit()
    return academic_service.subject_out(db, principal.user, subject.id)


@router.patch("/{subject_id}", response_model=SubjectOut)
def update_subject(
    subject_id: str,
    payload: SubjectUpdate,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict:
    subject = academic_service.update_subject(db, subject_id, payload)
    _audit(db, principal, "SUBJECT_UPDATED", "subject", subject.id, fields=sorted(payload.model_dump(exclude_none=True)))
    db.commit()
    return academic_service.subject_out(db, principal.user, subject.id)


@router.get("/teachers/all", response_model=list[dict])
def list_teachers(
    principal: Principal = Depends(require_hod), db: Session = Depends(get_db)
) -> list[dict]:
    teachers = db.execute(select(User).where(User.role == Role.TEACHER).order_by(User.full_name)).scalars().all()
    return [
        {
            "id": teacher.id,
            "email": teacher.email,
            "full_name": teacher.full_name,
            "department": teacher.department,
            "is_active": teacher.is_active,
            "mfa_enabled": teacher.mfa_enabled,
            "subject_ids": sorted(teacher_subject_ids(db, teacher.id)),
        }
        for teacher in teachers
    ]


@router.get("/{subject_id}/topics", response_model=list[TopicOut])
def list_topics(
    subject_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> list[dict]:
    ensure_subject_staff(db, principal.user, subject_id)
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
    _audit(db, principal, "TOPIC_CREATED", "topic", topic.id, subject_id=subject_id, name=topic.name)
    db.commit()
    return {"id": topic.id, "name": topic.name, "unit": topic.unit, "description": topic.description}


@router.patch("/{subject_id}/topics/{topic_id}", response_model=TopicOut)
def update_topic(
    subject_id: str,
    topic_id: str,
    payload: TopicUpdate,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    topic = academic_service.update_topic(db, subject_id, topic_id, payload, principal.user)
    _audit(db, principal, "TOPIC_UPDATED", "topic", topic.id, subject_id=subject_id)
    db.commit()
    return {"id": topic.id, "name": topic.name, "unit": topic.unit, "description": topic.description}


@router.delete("/{subject_id}/topics/{topic_id}")
def delete_topic(
    subject_id: str,
    topic_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    academic_service.delete_topic(db, subject_id, topic_id, principal.user)
    _audit(db, principal, "TOPIC_DELETED", "topic", topic_id, subject_id=subject_id)
    db.commit()
    return {"deleted": True}


@router.post("/{subject_id}/teachers/{teacher_id}", status_code=201)
def assign_teacher(
    subject_id: str,
    teacher_id: str,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    subject = ensure_subject_staff(db, principal.user, subject_id)
    academic_service.assign_teacher(db, subject.id, teacher_id)
    _audit(db, principal, "TEACHER_ASSIGNED_TO_SUBJECT", "subject", subject.id, teacher_id=teacher_id)
    db.commit()
    return {"status": "assigned", "subject_id": subject.id, "teacher_id": teacher_id}


@router.delete("/{subject_id}/teachers/{teacher_id}")
def unassign_teacher(
    subject_id: str,
    teacher_id: str,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    subject = ensure_subject_staff(db, principal.user, subject_id)
    academic_service.unassign_teacher(db, subject.id, teacher_id)
    _audit(db, principal, "TEACHER_UNASSIGNED_FROM_SUBJECT", "subject", subject.id, teacher_id=teacher_id)
    db.commit()
    return {"status": "unassigned", "subject_id": subject.id, "teacher_id": teacher_id}


@router.get("/{subject_id}/students", response_model=list[dict])
def list_students(
    subject_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> list[dict]:
    ensure_subject_staff(db, principal.user, subject_id)
    return academic_service.list_students(db, subject_id)


@router.post("/{subject_id}/students", status_code=201)
def enroll_students(
    subject_id: str,
    payload: EnrollRequest,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    added = academic_service.enroll_students(db, subject_id, payload.student_ids)
    _audit(db, principal, "STUDENTS_ENROLLED", "subject", subject_id, added=added, requested=len(payload.student_ids))
    db.commit()
    return {"enrolled": added}


@router.delete("/{subject_id}/students/{student_id}")
def unenroll_student(
    subject_id: str,
    student_id: str,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    academic_service.unenroll_student(db, subject_id, student_id)
    _audit(db, principal, "STUDENT_UNENROLLED", "subject", subject_id, student_id=student_id)
    db.commit()
    return {"unenrolled": True}
