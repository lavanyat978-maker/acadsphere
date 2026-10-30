"""Account administration (HOD) and bootstrap (CLI).

Accounts are never self-registered. The HOD creates them with a one-time
temporary password; the user must replace it at first sign-in.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.security import (
    generate_temporary_password,
    hash_password,
    password_policy_problem,
)
from app.models.enums import Role, Severity
from app.models.tables import StudentSubject, Subject, SubjectTeacher, User
from app.schemas.users import UserCreate, UserUpdate
from app.services.audit_service import record_audit, record_security_event


def user_out(db: Session, user: User) -> dict[str, Any]:
    subject_ids: list[str] = []
    if user.role == Role.TEACHER:
        subject_ids = list(
            db.execute(select(SubjectTeacher.subject_id).where(SubjectTeacher.teacher_id == user.id)).scalars()
        )
    elif user.role == Role.STUDENT:
        subject_ids = list(
            db.execute(select(StudentSubject.subject_id).where(StudentSubject.student_id == user.id)).scalars()
        )
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "department": user.department,
        "is_active": user.is_active,
        "mfa_enabled": user.mfa_enabled,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "subject_ids": sorted(subject_ids),
    }


def list_users(db: Session, *, role: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
    query = select(User)
    if role:
        query = query.where(User.role == role)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(User.email).like(like), func.lower(User.full_name).like(like)))
    users = db.execute(query.order_by(User.role, User.full_name)).scalars().all()
    return [user_out(db, user) for user in users]


def _active_hod_count(db: Session) -> int:
    return int(
        db.execute(select(func.count(User.id)).where(User.role == Role.HOD, User.is_active.is_(True))).scalar_one()
    )


def create_user(
    db: Session,
    payload: UserCreate,
    *,
    actor: User | None,
    ip: str = "",
    force_change: bool = True,
) -> tuple[User, str | None]:
    """Create an account. Returns the user and the temporary password if one was generated."""
    email = payload.email.strip().lower()
    if db.execute(select(User.id).where(User.email == email)).first():
        raise Conflict("An account with this email already exists.", "USER_EXISTS")
    temporary: str | None = None
    password = payload.password
    if password:
        problem = password_policy_problem(password)
        if problem:
            raise AppError("WEAK_PASSWORD", problem, 400)
    else:
        temporary = generate_temporary_password()
        password = temporary
    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        role=payload.role,
        department=payload.department,
        password_hash=hash_password(password),
        must_change_password=force_change,
    )
    db.add(user)
    db.flush()
    for subject_id in dict.fromkeys(payload.subject_ids):
        if db.get(Subject, subject_id) is None:
            raise NotFound("Subject")
        if user.role == Role.TEACHER:
            db.add(SubjectTeacher(subject_id=subject_id, teacher_id=user.id))
        elif user.role == Role.STUDENT:
            db.add(StudentSubject(subject_id=subject_id, student_id=user.id))
    record_audit(
        db,
        event="USER_CREATED",
        actor=actor,
        actor_role=None if actor else "SYSTEM",
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        metadata={"email": email, "role": user.role, "subjects": len(payload.subject_ids)},
    )
    db.flush()
    return user, temporary


def update_user(db: Session, actor: User, user_id: str, payload: UserUpdate, ip: str = "") -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User")
    changes = payload.model_dump(exclude_none=True)
    if "is_active" in changes and not changes["is_active"]:
        if user.id == actor.id:
            raise Forbidden("You cannot deactivate your own account.", code="SELF_DEACTIVATION")
        if user.role == Role.HOD and user.is_active and _active_hod_count(db) <= 1:
            raise Conflict("At least one active HOD account must remain.", "LAST_HOD")
    for field, value in changes.items():
        setattr(user, field, value)
    if changes.get("is_active") is False:
        user.token_version += 1  # end every live session immediately
    record_audit(
        db,
        event="USER_UPDATED",
        actor=actor,
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        metadata={"fields": sorted(changes)},
    )
    db.flush()
    return user


def reset_password(db: Session, actor: User, user_id: str, ip: str = "") -> str:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User")
    if user.id == actor.id:
        raise Forbidden("Use Account settings to change your own password.", code="SELF_RESET")
    temporary = generate_temporary_password()
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    user.token_version += 1
    record_audit(db, event="USER_PASSWORD_RESET", actor=actor, resource_type="user", resource_id=user.id, ip=ip)
    record_security_event(
        db,
        event_type="PASSWORD_RESET_BY_HOD",
        summary=f"Password reset for {user.email}",
        severity=Severity.INFO,
        actor_id=actor.id,
        ip=ip,
    )
    db.flush()
    return temporary


def reset_mfa(db: Session, actor: User, user_id: str, ip: str = "") -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User")
    if user.id == actor.id:
        raise Forbidden("Another HOD must reset your authenticator.", code="SELF_RESET")
    user.mfa_enabled = False
    user.mfa_secret_enc = None
    user.mfa_last_step = None
    user.token_version += 1
    record_audit(db, event="USER_MFA_RESET", actor=actor, resource_type="user", resource_id=user.id, ip=ip)
    record_security_event(
        db,
        event_type="MFA_RESET_BY_HOD",
        summary=f"Authenticator reset for {user.email}",
        severity=Severity.WARNING,
        actor_id=actor.id,
        ip=ip,
    )
    db.flush()
    return user


def unlock(db: Session, actor: User, user_id: str, ip: str = "") -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User")
    user.failed_login_count = 0
    user.locked_until = None
    record_audit(db, event="USER_UNLOCKED", actor=actor, resource_type="user", resource_id=user.id, ip=ip)
    db.flush()
    return user


IMPORT_MAX_ROWS = 500
IMPORT_COLUMNS = ("email", "full_name", "role", "department", "subjects")


def import_users_csv(db: Session, actor: User, data: bytes, ip: str = "") -> list[dict[str, Any]]:
    """Create accounts from a CSV file. All rows are validated before any is created.

    Columns: email, full_name (required); role (STUDENT/TEACHER/HOD, default
    STUDENT), department, subjects (subject codes separated by ";").
    Returns one row per created account with its one-time password.
    """
    import csv
    import io

    from email_validator import EmailNotValidError, validate_email

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("CSV_ENCODING", "Save the file as UTF-8 CSV.", 400) from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = [(name or "").strip().lower() for name in (reader.fieldnames or [])]
    if "email" not in headers or "full_name" not in headers:
        raise AppError("CSV_HEADERS", "The first row must contain at least the columns email and full_name.", 400)
    reader.fieldnames = headers

    subjects_by_code = {subject.code.upper(): subject.id for subject in db.execute(select(Subject)).scalars()}
    existing = {email for email in db.execute(select(User.email)).scalars()}
    rows: list[UserCreate] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line, raw in enumerate(reader, start=2):
        record = {key: (value or "").strip() for key, value in raw.items() if key in IMPORT_COLUMNS}
        if not any(record.values()):
            continue
        if len(rows) + len(errors) >= IMPORT_MAX_ROWS:
            raise AppError("CSV_TOO_LARGE", f"Import at most {IMPORT_MAX_ROWS} rows per file.", 400)
        email = record.get("email", "").lower()
        try:
            email = validate_email(email, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            errors.append({"row": line, "error": f"Invalid email '{record.get('email', '')}'"})
            continue
        if email in existing or email in seen:
            errors.append({"row": line, "error": f"{email} already exists"})
            continue
        role = (record.get("role") or "STUDENT").upper()
        if role not in {item.value for item in Role}:
            errors.append({"row": line, "error": f"Unknown role '{record.get('role')}'"})
            continue
        codes = [code.strip().upper() for code in (record.get("subjects") or "").replace("|", ";").split(";") if code.strip()]
        unknown = [code for code in codes if code not in subjects_by_code]
        if unknown:
            errors.append({"row": line, "error": f"Unknown subject code(s): {', '.join(unknown)}"})
            continue
        if len(record.get("full_name", "")) < 2:
            errors.append({"row": line, "error": "full_name is required"})
            continue
        seen.add(email)
        rows.append(
            UserCreate(
                email=email,
                full_name=record["full_name"],
                role=Role(role),
                department=record.get("department") or "Computer Science",
                subject_ids=[subjects_by_code[code] for code in codes],
            )
        )
    if errors:
        raise AppError(
            "CSV_INVALID",
            f"{len(errors)} row(s) need fixing. Nothing was imported.",
            422,
            details=errors[:100],
        )
    if not rows:
        raise AppError("CSV_EMPTY", "The file has no accounts to import.", 400)

    created: list[dict[str, Any]] = []
    for payload in rows:
        user, temporary = create_user(db, payload, actor=actor, ip=ip)
        created.append(
            {
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "temporary_password": temporary,
            }
        )
    record_audit(
        db,
        event="USERS_IMPORTED",
        actor=actor,
        resource_type="user",
        ip=ip,
        metadata={"count": len(created)},
    )
    db.flush()
    return created
