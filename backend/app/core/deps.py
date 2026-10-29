"""Authentication / authorization dependencies.

Every protected endpoint resolves the caller from a signed JWT (cookie or
Bearer), re-loads the user from the database, re-checks role and token version,
and writes an audit record when access is denied. UI hiding is never trusted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import jwt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Forbidden, RateLimited, Unauthorized
from app.core.runtime import client_ip, rate_limiter
from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import Role
from app.models.tables import User
from app.services.audit_service import record_audit


@dataclass
class Principal:
    user: User
    claims: dict
    ip: str
    user_agent: str

    @property
    def id(self) -> str:
        return self.user.id

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def mfa_verified(self) -> bool:
        return bool(self.claims.get("mfa"))


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return request.cookies.get("access_token")


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    token = _extract_token(request)
    if not token:
        raise Unauthorized()
    try:
        claims = decode_token(token, expected="access")
    except jwt.ExpiredSignatureError:
        raise Unauthorized("Your session has expired. Please sign in again.", "SESSION_EXPIRED")
    except jwt.InvalidTokenError:
        raise Unauthorized("Invalid session token.", "INVALID_TOKEN")
    user = db.get(User, claims.get("sub"))
    if user is None or not user.is_active:
        raise Unauthorized("Account is unavailable.", "ACCOUNT_INACTIVE")
    if int(claims.get("tv", 0)) != user.token_version:
        raise Unauthorized("Your session was invalidated. Please sign in again.", "SESSION_REVOKED")
    if claims.get("role") != user.role:
        raise Unauthorized("Session role mismatch. Please sign in again.", "ROLE_MISMATCH")
    return Principal(
        user=user,
        claims=claims,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:255],
    )


def require_role(*roles: Role | str) -> Callable[[Principal, Session], Principal]:
    allowed = {str(role) for role in roles}

    def dependency(
        request: Request,
        principal: Principal = Depends(get_principal),
        db: Session = Depends(get_db),
    ) -> Principal:
        if principal.role not in allowed:
            record_audit(
                db,
                event="UNAUTHORIZED_ACCESS_ATTEMPT",
                actor=principal.user,
                resource_type=request.url.path,
                resource_id="",
                success=False,
                ip=principal.ip,
                user_agent=principal.user_agent,
                metadata={
                    "method": request.method,
                    "required_roles": sorted(allowed),
                    "actual_role": principal.role,
                },
            )
            db.commit()
            raise Forbidden()
        return principal

    return dependency


def require_mfa(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.mfa_verified:
        raise Forbidden(
            "Multi-factor authentication is required for this action.",
            code="MFA_REQUIRED",
        )
    if not principal.user.mfa_enabled:
        raise Forbidden(
            "Enroll an authenticator application before using release functions.",
            code="MFA_NOT_ENROLLED",
        )
    return principal


require_student = require_role(Role.STUDENT)
require_teacher = require_role(Role.TEACHER)
require_hod = require_role(Role.HOD)
require_staff = require_role(Role.TEACHER, Role.HOD)


def enforce_rate_limit(request: Request, bucket: str, limit: int, window_seconds: int = 60) -> None:
    key = f"{bucket}:{client_ip(request)}"
    ok, retry = rate_limiter.hit(key, limit, window_seconds)
    if not ok:
        raise RateLimited(retry)


def rate_limit(bucket: str, limit_attr: str) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        limit = int(getattr(settings, limit_attr))
        enforce_rate_limit(request, bucket, limit)

    return dependency
