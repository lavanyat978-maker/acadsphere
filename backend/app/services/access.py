"""Central role-based access rules.

One place answers "may this user see / change this thing?" so routers and
services stop re-deriving it. The rules:

* HOD is a superset of a teacher in every subject, plus administration,
  release and audit.
* A teacher works inside the subjects they are assigned to. They can *view*
  everything in those subjects (including co-teachers' exams and tests) but can
  only *change* what they created.
* A student sees published practice tests only for subjects they are enrolled in,
  and never any internal examination data.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound
from app.models.enums import Role
from app.models.tables import (
    InternalExam,
    PracticeTest,
    StudentSubject,
    Subject,
    SubjectTeacher,
    User,
)


def is_hod(user: User) -> bool:
    return user.role == Role.HOD


def is_staff(user: User) -> bool:
    return user.role in {Role.TEACHER, Role.HOD}


def teacher_subject_ids(db: Session, teacher_id: str) -> set[str]:
    return set(
        db.execute(
            select(SubjectTeacher.subject_id).where(SubjectTeacher.teacher_id == teacher_id)
        ).scalars()
    )


def student_subject_ids(db: Session, student_id: str) -> set[str]:
    return set(
        db.execute(
            select(StudentSubject.subject_id).where(StudentSubject.student_id == student_id)
        ).scalars()
    )


def visible_subject_ids(db: Session, user: User) -> set[str] | None:
    """Subjects the user works in. ``None`` means every subject (HOD)."""
    if is_hod(user):
        return None
    if user.role == Role.TEACHER:
        return teacher_subject_ids(db, user.id)
    return student_subject_ids(db, user.id)


def in_subject(db: Session, user: User, subject_id: str) -> bool:
    allowed = visible_subject_ids(db, user)
    return allowed is None or subject_id in allowed


def ensure_subject_staff(db: Session, user: User, subject_id: str) -> Subject:
    """Staff access to a subject: HOD always, teacher only when assigned."""
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise NotFound("Subject")
    if is_hod(user):
        return subject
    if user.role == Role.TEACHER and subject_id in teacher_subject_ids(db, user.id):
        return subject
    raise Forbidden("You are not assigned to this subject.", code="SUBJECT_NOT_ASSIGNED")


# --- Internal examinations ------------------------------------------------
def can_view_exam(db: Session, user: User, exam: InternalExam) -> bool:
    if is_hod(user):
        return True
    if user.role != Role.TEACHER:
        return False
    return exam.created_by == user.id or exam.subject_id in teacher_subject_ids(db, user.id)


def can_edit_exam(user: User, exam: InternalExam) -> bool:
    return is_hod(user) or (user.role == Role.TEACHER and exam.created_by == user.id)


def ensure_view_exam(db: Session, user: User, exam_id: str) -> InternalExam:
    exam = db.get(InternalExam, exam_id)
    if exam is None:
        raise NotFound("Examination")
    if user.role == Role.STUDENT:
        raise Forbidden("Students do not have access to internal examinations.")
    if not can_view_exam(db, user, exam):
        raise Forbidden("This examination belongs to a subject you are not assigned to.", code="NOT_EXAM_SUBJECT")
    return exam


def ensure_edit_exam(db: Session, user: User, exam_id: str) -> InternalExam:
    exam = ensure_view_exam(db, user, exam_id)
    if not can_edit_exam(user, exam):
        raise Forbidden(
            "Only the teacher who created this examination, or the HOD, can change it.",
            code="NOT_EXAM_OWNER",
        )
    return exam


# --- Practice tests -------------------------------------------------------
def can_view_test(db: Session, user: User, test: PracticeTest) -> bool:
    if is_hod(user):
        return True
    if user.role == Role.TEACHER:
        return test.created_by == user.id or test.subject_id in teacher_subject_ids(db, user.id)
    return False


def can_edit_test(user: User, test: PracticeTest) -> bool:
    return is_hod(user) or (user.role == Role.TEACHER and test.created_by == user.id)


def ensure_view_test(db: Session, user: User, test_id: str) -> PracticeTest:
    test = db.get(PracticeTest, test_id)
    if test is None:
        raise NotFound("Practice test")
    if not can_view_test(db, user, test):
        raise Forbidden("This practice test belongs to a subject you are not assigned to.", code="NOT_TEST_SUBJECT")
    return test


def ensure_edit_test(db: Session, user: User, test_id: str) -> PracticeTest:
    test = ensure_view_test(db, user, test_id)
    if not can_edit_test(user, test):
        raise Forbidden(
            "Only the teacher who created this practice test, or the HOD, can change it.",
            code="NOT_TEST_OWNER",
        )
    return test
