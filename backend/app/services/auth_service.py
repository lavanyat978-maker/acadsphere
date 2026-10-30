"""Authentication service: Argon2id password checks, TOTP MFA, session issuance."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, Conflict, Forbidden, Unauthorized
from app.core.security import (
    burn_password_check,
    create_token,
    hash_password,
    new_totp_secret,
    open_secret,
    password_needs_rehash,
    password_policy_problem,
    seal_secret,
    totp_uri,
    utcnow,
    verify_password,
    totp_step,
)
from app.core.timeutils import ensure_utc
from app.models.enums import Role, Severity
from app.models.tables import User
from app.services.audit_service import record_audit, record_security_event


class AccountLocked(AppError):
    def __init__(self, minutes: int) -> None:
        super().__init__(
            "ACCOUNT_LOCKED",
            f"Too many failed attempts. Try again in about {minutes} minute(s).",
            423,
        )


def _user_out(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "department": user.department,
        "mfa_enabled": user.mfa_enabled,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at,
    }


def _register_failure(db: Session, user: User, ip: str, ua: str, reason: str) -> None:
    user.failed_login_count += 1
    locked = False
    if user.failed_login_count >= settings.MAX_FAILED_LOGINS:
        user.locked_until = utcnow() + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
        user.failed_login_count = 0
        locked = True
        record_security_event(
            db,
            event_type="ACCOUNT_LOCKED",
            summary=f"Account locked after repeated failed logins: {user.email}",
            severity=Severity.WARNING,
            actor_id=user.id,
            ip=ip,
            detail={"reason": reason},
        )
    record_audit(
        db,
        event="LOGIN_FAILED",
        actor=user,
        resource_type="auth",
        success=False,
        ip=ip,
        user_agent=ua,
        metadata={"reason": reason, "account_locked": locked},
    )


def login(db: Session, email: str, password: str, ip: str, ua: str) -> dict[str, Any]:
    normalised = email.strip().lower()
    user = db.execute(select(User).where(User.email == normalised)).scalar_one_or_none()

    if user is None:
        burn_password_check(password)
        record_audit(
            db,
            event="LOGIN_FAILED",
            actor_role="ANONYMOUS",
            resource_type="auth",
            success=False,
            ip=ip,
            user_agent=ua,
            metadata={"reason": "unknown_account"},
        )
        db.commit()
        raise Unauthorized("Email or password is incorrect.")

    if not user.is_active:
        record_audit(
            db,
            event="LOGIN_BLOCKED_INACTIVE",
            actor=user,
            resource_type="auth",
            success=False,
            ip=ip,
            user_agent=ua,
        )
        db.commit()
        raise Unauthorized("Email or password is incorrect.")

    locked_until = ensure_utc(user.locked_until)
    if locked_until is not None and locked_until > utcnow():
        minutes = max(1, int((locked_until - utcnow()).total_seconds() // 60) + 1)
        record_audit(
            db,
            event="LOGIN_BLOCKED_LOCKED",
            actor=user,
            resource_type="auth",
            success=False,
            ip=ip,
            user_agent=ua,
        )
        db.commit()
        raise AccountLocked(minutes)

    if not verify_password(password, user.password_hash):
        _register_failure(db, user, ip, ua, "bad_password")
        db.commit()
        raise Unauthorized("Email or password is incorrect.")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    if user.role == Role.HOD:
        if not user.mfa_enabled or not user.mfa_secret_enc:
            # First sign-in for an HOD: the password is proven, so hand out an
            # enrolment challenge. The session is only issued once the HOD
            # proves possession of the new authenticator (`/auth/mfa/enroll`).
            secret = open_secret(user.mfa_secret_enc) if user.mfa_secret_enc else None
            if not secret:
                secret = new_totp_secret()
                user.mfa_secret_enc = seal_secret(secret)
                user.mfa_last_step = None
            user.mfa_enabled = False
            user.failed_login_count = 0
            token, expires = create_token(
                user.id, user.role, kind="mfa_enroll", token_version=user.token_version
            )
            record_audit(
                db,
                event="LOGIN_MFA_ENROLLMENT_REQUIRED",
                actor=user,
                resource_type="auth",
                ip=ip,
                user_agent=ua,
            )
            db.commit()
            return {
                "mfa_required": True,
                "enrollment_required": True,
                "mfa_token": token,
                "expires_at": expires,
                "method": "TOTP",
                "secret": secret,
                "otpauth_uri": totp_uri(secret, user.email),
            }
        user.failed_login_count = 0
        token, expires = create_token(
            user.id, user.role, kind="mfa_challenge", mfa_verified=False,
            token_version=user.token_version,
        )
        record_audit(
            db,
            event="LOGIN_MFA_CHALLENGE",
            actor=user,
            resource_type="auth",
            ip=ip,
            user_agent=ua,
        )
        db.commit()
        return {
            "mfa_required": True,
            "mfa_token": token,
            "expires_at": expires,
            "method": "TOTP",
        }

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    token, expires = create_token(
        user.id, user.role, mfa_verified=False, token_version=user.token_version
    )
    record_audit(
        db,
        event="LOGIN_SUCCESS",
        actor=user,
        resource_type="auth",
        ip=ip,
        user_agent=ua,
        metadata={"method": "password"},
    )
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires,
        "user": _user_out(user),
        "mfa_required": False,
    }


def _challenge_user(
    db: Session, mfa_token: str, kind: str, ip: str, ua: str
) -> User:
    """Resolve and sanity-check the user behind an MFA challenge or enrolment token."""
    from app.core.security import decode_token

    try:
        claims = decode_token(mfa_token, expected=kind)  # type: ignore[arg-type]
    except Exception:
        record_audit(
            db,
            event="MFA_CHALLENGE_INVALID",
            actor_role="ANONYMOUS",
            resource_type="auth",
            success=False,
            ip=ip,
            user_agent=ua,
        )
        db.commit()
        raise Unauthorized("The MFA challenge expired. Sign in again.", "MFA_CHALLENGE_EXPIRED")

    user = db.get(User, claims.get("sub"))
    if user is None or not user.is_active or not user.mfa_secret_enc:
        raise Unauthorized("MFA is not available for this account.")
    if int(claims.get("tv", 0)) != user.token_version:
        raise Unauthorized("The MFA challenge is no longer valid. Sign in again.", "MFA_CHALLENGE_EXPIRED")
    # Lockout applies to code guessing too, not only to passwords.
    locked_until = ensure_utc(user.locked_until)
    if locked_until is not None and locked_until > utcnow():
        minutes = max(1, int((locked_until - utcnow()).total_seconds() // 60) + 1)
        record_audit(
            db, event="MFA_BLOCKED_LOCKED", actor=user, resource_type="auth", success=False, ip=ip, user_agent=ua
        )
        db.commit()
        raise AccountLocked(minutes)
    return user


def _accept_code(user: User, code: str) -> bool:
    """Check a TOTP code and consume its time-step. Replays of a used code fail."""
    if not user.mfa_secret_enc:
        return False
    step = totp_step(open_secret(user.mfa_secret_enc), code)
    if step is None or (user.mfa_last_step is not None and step <= user.mfa_last_step):
        return False
    user.mfa_last_step = step
    return True


def _reject_code(db: Session, user: User, ip: str, ua: str) -> None:
    _register_failure(db, user, ip, ua, "bad_totp")
    record_security_event(
        db,
        event_type="MFA_FAILURE",
        summary=f"Invalid TOTP code for {user.email}",
        severity=Severity.WARNING,
        actor_id=user.id,
        ip=ip,
    )
    db.commit()
    raise Unauthorized("That verification code is not valid.", "MFA_INVALID_CODE")


def _issue_session(db: Session, user: User, ip: str, ua: str, event: str) -> dict[str, Any]:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    token, expires = create_token(
        user.id, user.role, mfa_verified=True, token_version=user.token_version
    )
    record_audit(
        db,
        event=event,
        actor=user,
        resource_type="auth",
        ip=ip,
        user_agent=ua,
        metadata={"method": "password+totp"},
    )
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires,
        "user": _user_out(user),
        "mfa_required": False,
    }


def verify_mfa(db: Session, mfa_token: str, code: str, ip: str, ua: str) -> dict[str, Any]:
    user = _challenge_user(db, mfa_token, "mfa_challenge", ip, ua)
    if not user.mfa_enabled:
        raise Unauthorized("MFA is not available for this account.")
    if not _accept_code(user, code):
        _reject_code(db, user, ip, ua)
    return _issue_session(db, user, ip, ua, "LOGIN_MFA_SUCCESS")


def complete_enrollment_login(db: Session, mfa_token: str, code: str, ip: str, ua: str) -> dict[str, Any]:
    """Finish the first-login enrolment started by `login` and open the session."""
    user = _challenge_user(db, mfa_token, "mfa_enroll", ip, ua)
    if user.mfa_enabled:
        raise Conflict("MFA is already enrolled. Sign in again.", "MFA_ALREADY_ENABLED")
    if not _accept_code(user, code):
        _reject_code(db, user, ip, ua)
    user.mfa_enabled = True
    user.token_version += 1
    record_audit(db, event="MFA_ENROLLED", actor=user, resource_type="auth", ip=ip, user_agent=ua)
    record_security_event(
        db,
        event_type="MFA_ENROLLED",
        summary=f"TOTP enrolled at first sign-in for {user.email}",
        severity=Severity.INFO,
        actor_id=user.id,
        ip=ip,
    )
    return _issue_session(db, user, ip, ua, "LOGIN_MFA_SUCCESS")


def begin_mfa_enrollment(db: Session, user: User, ip: str) -> dict[str, Any]:
    if user.mfa_enabled:
        raise Conflict(
            "Multi-factor authentication is already active. Disable it first to enrol a new device.",
            "MFA_ALREADY_ENABLED",
        )
    secret = open_secret(user.mfa_secret_enc) if user.mfa_secret_enc else None
    if not secret:
        secret = new_totp_secret()
        user.mfa_secret_enc = seal_secret(secret)
        user.mfa_last_step = None
    user.mfa_enabled = False
    record_audit(db, event="MFA_ENROLLMENT_STARTED", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return {
        "secret": secret,
        "otpauth_uri": totp_uri(secret, user.email),
        "instructions": "Add this account to Google Authenticator/Authy, then confirm with a 6-digit code.",
    }


def confirm_mfa_enrollment(db: Session, user: User, code: str, ip: str) -> dict[str, Any]:
    if user.mfa_enabled:
        raise Conflict("Multi-factor authentication is already active.", "MFA_ALREADY_ENABLED")
    if not user.mfa_secret_enc:
        raise AppError("MFA_NOT_STARTED", "Start enrollment before confirming.", 400)
    secret = open_secret(user.mfa_secret_enc)
    if not _accept_code(user, code):
        raise AppError("MFA_INVALID_CODE", "That verification code is not valid.", 400)
    user.mfa_enabled = True
    user.token_version += 1
    record_audit(db, event="MFA_ENROLLED", actor=user, resource_type="auth", ip=ip)
    record_security_event(
        db,
        event_type="MFA_ENROLLED",
        summary=f"TOTP enrolled for {user.email}",
        severity=Severity.INFO,
        actor_id=user.id,
        ip=ip,
    )
    db.commit()
    return {"mfa_enabled": True, "message": "Multi-factor authentication is now active. Sign in again."}


def disable_mfa(db: Session, user: User, code: str, ip: str) -> dict[str, Any]:
    if user.role == Role.HOD:
        raise Forbidden("MFA is mandatory for the HOD role and cannot be disabled.", code="MFA_MANDATORY")
    if not user.mfa_enabled or not user.mfa_secret_enc:
        raise Conflict("Multi-factor authentication is not active.", "MFA_NOT_ENABLED")
    if not _accept_code(user, code):
        record_audit(db, event="MFA_DISABLE_FAILED", actor=user, resource_type="auth", success=False, ip=ip)
        db.commit()
        raise AppError("MFA_INVALID_CODE", "That verification code is not valid.", 400)
    user.mfa_enabled = False
    user.mfa_secret_enc = None
    user.mfa_last_step = None
    user.token_version += 1
    record_audit(db, event="MFA_DISABLED", actor=user, resource_type="auth", ip=ip)
    record_security_event(
        db,
        event_type="MFA_DISABLED",
        summary=f"TOTP disabled by {user.email}",
        severity=Severity.WARNING,
        actor_id=user.id,
        ip=ip,
    )
    db.commit()
    return {"mfa_enabled": False, "message": "Multi-factor authentication disabled. Sign in again."}


def _unused_demo_code(user: User) -> dict[str, Any]:
    """Current code, or the next step's code when the current one was already used.

    Codes are single use, so a reviewer signing in twice within 30 s would
    otherwise be handed a code the server refuses. The next step is still
    inside the accepted drift window.
    """
    import pyotp

    totp = pyotp.TOTP(open_secret(user.mfa_secret_enc))
    step = int(utcnow().timestamp()) // totp.interval
    if user.mfa_last_step is not None and user.mfa_last_step >= step:
        step = user.mfa_last_step + 1
    return {
        "code": totp.generate_otp(step),
        "seconds_remaining": 30 - (int(utcnow().timestamp()) % 30),
        "warning": "DEMO_MODE only. This bypass is not available in production.",
    }


def demo_code_for_challenge(db: Session, mfa_token: str, ip: str) -> dict[str, Any]:
    """Return the current TOTP for the demo HOD using a valid MFA challenge.

    HOD sign-in always stops at the MFA challenge, so an authenticated session
    does not exist yet. This DEMO_MODE-only helper accepts the short-lived
    challenge instead and is limited to the seeded demo HOD account.
    """
    import pyotp

    from app.core.security import decode_token

    if not settings.DEMO_MODE:
        raise AppError("DEMO_DISABLED", "Demo helpers are disabled.", 403)
    try:
        claims = decode_token(mfa_token, expected="mfa_challenge")
    except Exception:
        raise Unauthorized("The MFA challenge expired. Sign in again.", "MFA_CHALLENGE_EXPIRED")
    user = db.get(User, claims.get("sub"))
    if user is None or user.role != Role.HOD or not user.mfa_secret_enc:
        raise AppError("DEMO_ONLY", "This helper is limited to HOD accounts.", 403)
    record_audit(db, event="DEMO_MFA_CODE_VIEWED", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return _unused_demo_code(user)


def demo_code(db: Session, user: User, ip: str) -> dict[str, Any]:
    """Demo-only helper so reviewers can complete the HOD MFA flow.

    Enabled only when DEMO_MODE=true and only for the seeded demo HOD account.
    Every use is audited. This is not part of the production security model.
    """
    import pyotp

    if not settings.DEMO_MODE:
        raise AppError("DEMO_DISABLED", "Demo helpers are disabled.", 403)
    if user.role != Role.HOD or not user.email.endswith("@praxis.demo") or not user.mfa_secret_enc:
        raise AppError("DEMO_ONLY", "This helper is limited to the demo HOD account.", 403)
    record_audit(db, event="DEMO_MFA_CODE_VIEWED", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return _unused_demo_code(user)


def change_password(
    db: Session, user: User, current: str, new: str, ip: str
) -> dict[str, Any]:
    if not verify_password(current, user.password_hash):
        record_audit(
            db, event="PASSWORD_CHANGE_FAILED", actor=user, resource_type="auth", success=False, ip=ip
        )
        db.commit()
        raise Unauthorized("Your current password is incorrect.")
    if verify_password(new, user.password_hash):
        raise AppError("PASSWORD_REUSED", "Choose a password different from the current one.", 400)
    problem = password_policy_problem(new)
    if problem:
        raise AppError("WEAK_PASSWORD", problem, 400)
    user.password_hash = hash_password(new)
    user.token_version += 1
    user.must_change_password = False
    record_audit(db, event="PASSWORD_CHANGED", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return {"message": "Password updated. Other sessions have been revoked."}


def logout(db: Session, user: User, ip: str) -> dict[str, Any]:
    user.token_version += 1
    record_audit(db, event="LOGOUT", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return {"message": "Signed out."}
