"""Authentication service: Argon2id password checks, TOTP MFA, session issuance."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, Unauthorized
from app.core.security import (
    burn_password_check,
    create_token,
    hash_password,
    new_totp_secret,
    open_secret,
    password_needs_rehash,
    seal_secret,
    totp_uri,
    utcnow,
    verify_password,
    verify_totp,
)
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

    locked_until = user.locked_until
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
            record_audit(
                db,
                event="LOGIN_BLOCKED_MFA_NOT_ENROLLED",
                actor=user,
                resource_type="auth",
                success=False,
                ip=ip,
                user_agent=ua,
            )
            db.commit()
            raise AppError(
                "MFA_NOT_ENROLLED",
                "HOD accounts must enroll multi-factor authentication before signing in. "
                "Use the MFA enrollment flow with your credentials.",
                403,
            )
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


def verify_mfa(db: Session, mfa_token: str, code: str, ip: str, ua: str) -> dict[str, Any]:
    from app.core.security import decode_token

    try:
        claims = decode_token(mfa_token, expected="mfa_challenge")
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
    if user is None or not user.is_active or not user.mfa_enabled or not user.mfa_secret_enc:
        raise Unauthorized("MFA is not available for this account.")

    secret = open_secret(user.mfa_secret_enc)
    if not verify_totp(secret, code):
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

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    token, expires = create_token(
        user.id, user.role, mfa_verified=True, token_version=user.token_version
    )
    record_audit(
        db,
        event="LOGIN_MFA_SUCCESS",
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


def begin_mfa_enrollment(db: Session, user: User, ip: str) -> dict[str, Any]:
    secret = open_secret(user.mfa_secret_enc) if user.mfa_secret_enc else None
    if not secret:
        secret = new_totp_secret()
        user.mfa_secret_enc = seal_secret(secret)
    user.mfa_enabled = False
    record_audit(db, event="MFA_ENROLLMENT_STARTED", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return {
        "secret": secret,
        "otpauth_uri": totp_uri(secret, user.email),
        "instructions": "Add this account to Google Authenticator/Authy, then confirm with a 6-digit code.",
    }


def confirm_mfa_enrollment(db: Session, user: User, code: str, ip: str) -> dict[str, Any]:
    if not user.mfa_secret_enc:
        raise AppError("MFA_NOT_STARTED", "Start enrollment before confirming.", 400)
    secret = open_secret(user.mfa_secret_enc)
    if not verify_totp(secret, code):
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
    secret = open_secret(user.mfa_secret_enc)
    totp = pyotp.TOTP(secret)
    record_audit(db, event="DEMO_MFA_CODE_VIEWED", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return {
        "code": totp.now(),
        "seconds_remaining": 30 - (int(utcnow().timestamp()) % 30),
        "warning": "DEMO_MODE only. This bypass is not available in production.",
    }


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
    secret = open_secret(user.mfa_secret_enc)
    totp = pyotp.TOTP(secret)
    record_audit(db, event="DEMO_MFA_CODE_VIEWED", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return {
        "code": totp.now(),
        "seconds_remaining": 30 - (int(utcnow().timestamp()) % 30),
        "warning": "DEMO_MODE only. This bypass is not available in production.",
    }


def change_password(
    db: Session, user: User, current: str, new: str, ip: str
) -> dict[str, Any]:
    if not verify_password(current, user.password_hash):
        record_audit(
            db, event="PASSWORD_CHANGE_FAILED", actor=user, resource_type="auth", success=False, ip=ip
        )
        db.commit()
        raise Unauthorized("Your current password is incorrect.")
    user.password_hash = hash_password(new)
    user.token_version += 1
    record_audit(db, event="PASSWORD_CHANGED", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return {"message": "Password updated. Other sessions have been revoked."}


def logout(db: Session, user: User, ip: str) -> dict[str, Any]:
    user.token_version += 1
    record_audit(db, event="LOGOUT", actor=user, resource_type="auth", ip=ip)
    db.commit()
    return {"message": "Signed out."}
