"""Subjects, topics, teacher assignment and student enrolment."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.models.enums import Role
from app.models.tables import (
    ExamTopic,
    PracticeQuestion,
    PracticeTestTopic,
    StudentSubject,
    Subject,
    SubjectTeacher,
    Topic,
    User,
)
from app.schemas.academic import SubjectCreate, SubjectUpdate, TopicCreate, TopicUpdate
from app.services.access import (
    ensure_subject_staff,
    is_hod,
    teacher_subject_ids,
    visible_subject_ids,
)

# Kept as names other modules import.
ensure_subject_access = ensure_subject_staff


def teacher_has_subject(db: Session, teacher: User, subject_id: str) -> bool:
    if is_hod(teacher):
        return True
    return subject_id in teacher_subject_ids(db, teacher.id)


def _topic_out(topic: Topic) -> dict:
    return {"id": topic.id, "name": topic.name, "unit": topic.unit, "description": topic.description}


def list_subjects(db: Session, user: User) -> list[dict]:
    """Staff see every subject (flagged with `assigned_to_me`); students see their enrolments."""
    query = select(Subject).order_by(Subject.name)
    if user.role == Role.STUDENT:
        allowed = visible_subject_ids(db, user) or set()
        if not allowed:
            return []
        query = query.where(Subject.id.in_(allowed))
    subjects = db.execute(query).scalars().all()
    mine = visible_subject_ids(db, user)
    student_counts = dict(
        db.execute(
            select(StudentSubject.subject_id, func.count(StudentSubject.id)).group_by(StudentSubject.subject_id)
        ).all()
    )
    result = []
    for subject in subjects:
        teachers = db.execute(
            select(User.id, User.full_name)
            .join(SubjectTeacher, SubjectTeacher.teacher_id == User.id)
            .where(SubjectTeacher.subject_id == subject.id)
            .order_by(User.full_name)
        ).all()
        result.append(
            {
                "id": subject.id,
                "code": subject.code,
                "name": subject.name,
                "description": subject.description,
                "department": subject.department,
                "topics": [_topic_out(topic) for topic in sorted(subject.topics, key=lambda item: (item.unit, item.name))],
                "assigned_teachers": [name for _, name in teachers],
                "teacher_ids": [teacher_id for teacher_id, _ in teachers],
                "student_count": int(student_counts.get(subject.id, 0)),
                "assigned_to_me": mine is None or subject.id in mine,
            }
        )
    return result


def subject_out(db: Session, user: User, subject_id: str) -> dict:
    for row in list_subjects(db, user):
        if row["id"] == subject_id:
            return row
    raise NotFound("Subject")


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
        assign_teacher(db, subject.id, teacher_id)
    db.flush()
    return subject


def update_subject(db: Session, subject_id: str, payload: SubjectUpdate) -> Subject:
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise NotFound("Subject")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(subject, field, value)
    db.flush()
    return subject


def create_topic(db: Session, subject_id: str, payload: TopicCreate, actor: User) -> Topic:
    ensure_subject_staff(db, actor, subject_id)
    existing = db.execute(
        select(Topic).where(Topic.subject_id == subject_id, Topic.name == payload.name)
    ).scalar_one_or_none()
    if existing:
        raise Conflict("This topic already exists for the subject.", "TOPIC_EXISTS")
    topic = Topic(subject_id=subject_id, name=payload.name, unit=payload.unit, description=payload.description)
    db.add(topic)
    db.flush()
    return topic


def _topic_for(db: Session, subject_id: str, topic_id: str) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None or topic.subject_id != subject_id:
        raise NotFound("Topic")
    return topic


def update_topic(db: Session, subject_id: str, topic_id: str, payload: TopicUpdate, actor: User) -> Topic:
    ensure_subject_staff(db, actor, subject_id)
    topic = _topic_for(db, subject_id, topic_id)
    if payload.name and payload.name != topic.name:
        clash = db.execute(
            select(Topic.id).where(Topic.subject_id == subject_id, Topic.name == payload.name)
        ).first()
        if clash:
            raise Conflict("Another topic in this subject already has that name.", "TOPIC_EXISTS")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(topic, field, value)
    db.flush()
    return topic


def delete_topic(db: Session, subject_id: str, topic_id: str, actor: User) -> None:
    ensure_subject_staff(db, actor, subject_id)
    topic = _topic_for(db, subject_id, topic_id)
    in_use = any(
        db.execute(select(model.id).where(column == topic.id).limit(1)).first()
        for model, column in (
            (PracticeQuestion, PracticeQuestion.topic_id),
            (PracticeTestTopic, PracticeTestTopic.topic_id),
            (ExamTopic, ExamTopic.topic_id),
        )
    )
    if in_use:
        raise Conflict(
            "This topic is used by practice tests or examinations and cannot be deleted. Rename it instead.",
            "TOPIC_IN_USE",
        )
    db.delete(topic)
    db.flush()


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


def unassign_teacher(db: Session, subject_id: str, teacher_id: str) -> None:
    link = db.execute(
        select(SubjectTeacher).where(
            SubjectTeacher.subject_id == subject_id, SubjectTeacher.teacher_id == teacher_id
        )
    ).scalar_one_or_none()
    if link is None:
        raise NotFound("Teacher assignment")
    db.delete(link)
    db.flush()


def list_students(db: Session, subject_id: str) -> list[dict]:
    rows = db.execute(
        select(User, StudentSubject.enrolled_at)
        .join(StudentSubject, StudentSubject.student_id == User.id)
        .where(StudentSubject.subject_id == subject_id)
        .order_by(User.full_name)
    ).all()
    return [
        {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "department": user.department,
            "is_active": user.is_active,
            "enrolled_at": enrolled_at,
        }
        for user, enrolled_at in rows
    ]


def enroll_students(db: Session, subject_id: str, student_ids: list[str]) -> int:
    if db.get(Subject, subject_id) is None:
        raise NotFound("Subject")
    existing = set(
        db.execute(select(StudentSubject.student_id).where(StudentSubject.subject_id == subject_id)).scalars()
    )
    added = 0
    for student_id in dict.fromkeys(student_ids):
        student = db.get(User, student_id)
        if student is None or student.role != Role.STUDENT:
            raise NotFound("Student")
        if student_id in existing:
            continue
        db.add(StudentSubject(subject_id=subject_id, student_id=student_id))
        added += 1
    db.flush()
    return added


def unenroll_student(db: Session, subject_id: str, student_id: str) -> None:
    link = db.execute(
        select(StudentSubject).where(
            StudentSubject.subject_id == subject_id, StudentSubject.student_id == student_id
        )
    ).scalar_one_or_none()
    if link is None:
        raise NotFound("Enrolment")
    db.delete(link)
    db.flush()


def topics_for_subject(db: Session, subject_id: str) -> list[Topic]:
    return list(
        db.execute(select(Topic).where(Topic.subject_id == subject_id).order_by(Topic.name)).scalars().all()
    )
