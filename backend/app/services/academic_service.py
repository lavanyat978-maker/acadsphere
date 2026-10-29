"""Subjects, topics and teacher-subject assignment."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound
from app.models.enums import Role
from app.models.tables import Subject, SubjectTeacher, Topic, User
from app.schemas.academic import SubjectCreate, TopicCreate


def teacher_subject_ids(db: Session, teacher_id: str) -> set[str]:
    rows = db.execute(
        select(SubjectTeacher.subject_id).where(SubjectTeacher.teacher_id == teacher_id)
    ).scalars()
    return set(rows)


def teacher_has_subject(db: Session, teacher: User, subject_id: str) -> bool:
    if teacher.role == Role.HOD:
        return True
    return (
        db.execute(
            select(SubjectTeacher.id).where(
                SubjectTeacher.teacher_id == teacher.id,
                SubjectTeacher.subject_id == subject_id,
            )
        ).first()
        is not None
    )


def ensure_subject_access(db: Session, user: User, subject_id: str) -> Subject:
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise NotFound("Subject")
    if user.role == Role.HOD:
        return subject
    if user.role == Role.TEACHER and teacher_has_subject(db, user, subject_id):
        return subject
    raise Forbidden("You are not assigned to this subject.", code="SUBJECT_NOT_ASSIGNED")


def list_subjects(db: Session, user: User) -> list[dict]:
    subjects = db.execute(select(Subject).order_by(Subject.name)).scalars().all()
    assigned = teacher_subject_ids(db, user.id) if user.role == Role.TEACHER else set()
    result = []
    for subject in subjects:
        teachers = db.execute(
            select(User.full_name)
            .join(SubjectTeacher, SubjectTeacher.teacher_id == User.id)
            .where(SubjectTeacher.subject_id == subject.id)
        ).scalars().all()
        result.append(
            {
                "id": subject.id,
                "code": subject.code,
                "name": subject.name,
                "description": subject.description,
                "department": subject.department,
                "topics": [
                    {
                        "id": topic.id,
                        "name": topic.name,
                        "unit": topic.unit,
                        "description": topic.description,
                    }
                    for topic in sorted(subject.topics, key=lambda item: (item.unit, item.name))
                ],
                "assigned_teachers": list(teachers),
                "assigned_to_me": subject.id in assigned,
            }
        )
    return result


def create_subject(db: Session, payload: SubjectCreate, actor: User) -> Subject:
    if db.execute(select(Subject).where(Subject.code == payload.code.upper())).scalar_one_or_none():
        raise Conflict("A subject with this code already exists.", "SUBJECT_EXISTS")
    subject = Subject(
        code=payload.code.upper(),
        name=payload.name,
        description=payload.description,
        department=payload.department,
        created_by=actor.id,
    )
    db.add(subject)
    db.flush()
    for teacher_id in payload.teacher_ids:
        teacher = db.get(User, teacher_id)
        if teacher and teacher.role == Role.TEACHER:
            db.add(SubjectTeacher(subject_id=subject.id, teacher_id=teacher.id))
    db.flush()
    return subject


def create_topic(db: Session, subject_id: str, payload: TopicCreate, actor: User) -> Topic:
    ensure_subject_access(db, actor, subject_id)
    existing = db.execute(
        select(Topic).where(Topic.subject_id == subject_id, Topic.name == payload.name)
    ).scalar_one_or_none()
    if existing:
        raise Conflict("This topic already exists for the subject.", "TOPIC_EXISTS")
    topic = Topic(
        subject_id=subject_id,
        name=payload.name,
        unit=payload.unit,
        description=payload.description,
    )
    db.add(topic)
    db.flush()
    return topic


def assign_teacher(db: Session, subject_id: str, teacher_id: str) -> None:
    teacher = db.get(User, teacher_id)
    if teacher is None or teacher.role != Role.TEACHER:
        raise NotFound("Teacher")
    exists = db.execute(
        select(SubjectTeacher).where(
            SubjectTeacher.subject_id == subject_id, SubjectTeacher.teacher_id == teacher_id
        )
    ).scalar_one_or_none()
    if not exists:
        db.add(SubjectTeacher(subject_id=subject_id, teacher_id=teacher_id))
        db.flush()


def topics_for_subject(db: Session, subject_id: str) -> list[Topic]:
    return list(
        db.execute(select(Topic).where(Topic.subject_id == subject_id).order_by(Topic.name))
        .scalars()
        .all()
    )
