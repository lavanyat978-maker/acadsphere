from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_principal, rate_limit, require_student, require_teacher
from app.core.deps import Principal
from app.core.security import create_token
from app.db.session import get_db
from app.models.tables import User
from app.schemas.auth import (
    DemoCodeRequest,
    DemoCodeResponse,
    LoginRequest,
    MfaChallengeResponse,
    MfaConfirmRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    PasswordChangeRequest,
    TokenResponse,
    UserOut,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookies(response: Response, token: str, expires_at, ttl_minutes: int) -> str:
    csrf_token = secrets.token_urlsafe(32)
    max_age = ttl_minutes * 60
    response.set_cookie(
        "access_token",
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        "csrf_token",
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )
    return csrf_token


@router.post("/login", response_model=TokenResponse | MfaChallengeResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _: None = Depends(rate_limit("login", "RATE_LIMIT_LOGIN")),
) -> Any:
    result = auth_service.login(
        db,
        payload.email,
        payload.password,
        request.client.host if request.client else "unknown",
        request.headers.get("user-agent", ""),
    )
    if result.get("mfa_required"):
        return MfaChallengeResponse(**result)
    _set_session_cookies(response, result["access_token"], result["expires_at"], settings.ACCESS_TOKEN_TTL_MINUTES)
    return TokenResponse(**result)


@router.post("/mfa/verify", response_model=TokenResponse)
def verify_mfa(
    payload: MfaVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _: None = Depends(rate_limit("mfa", "RATE_LIMIT_MFA")),
) -> Any:
    result = auth_service.verify_mfa(
        db,
        payload.mfa_token,
        payload.code,
        request.client.host if request.client else "unknown",
        request.headers.get("user-agent", ""),
    )
    _set_session_cookies(response, result["access_token"], result["expires_at"], settings.ACCESS_TOKEN_TTL_MINUTES)
    return TokenResponse(**result)


@router.get("/csrf")
def csrf_token(response: Response) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        "csrf_token",
        token,
        max_age=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )
    return {"csrf_token": token}


@router.get("/me", response_model=UserOut)
def me(principal: Principal = Depends(get_principal)) -> Any:
    return principal.user


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    result = auth_service.logout(db, principal.user, principal.ip)
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("csrf_token", path="/")
    return result


@router.post("/password")
def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    _: None = Depends(rate_limit("password", "RATE_LIMIT_MFA")),
) -> dict[str, str]:
    result = auth_service.change_password(
        db, principal.user, payload.current_password, payload.new_password, principal.ip
    )
    token, expires_at = create_token(
        principal.user.id,
        principal.user.role,
        mfa_verified=principal.mfa_verified,
        token_version=principal.user.token_version,
    )
    _set_session_cookies(response, token, expires_at, settings.ACCESS_TOKEN_TTL_MINUTES)
    return result


@router.post("/mfa/setup", response_model=MfaSetupResponse)
def mfa_setup(
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> Any:
    return auth_service.begin_mfa_enrollment(db, principal.user, principal.ip)


@router.post("/mfa/confirm")
def mfa_confirm(
    payload: MfaConfirmRequest,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    _: None = Depends(rate_limit("mfa", "RATE_LIMIT_MFA")),
) -> dict[str, Any]:
    return auth_service.confirm_mfa_enrollment(db, principal.user, payload.code, principal.ip)


@router.post("/mfa/challenge-code", response_model=DemoCodeResponse)
def mfa_challenge_code(
    payload: DemoCodeRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(rate_limit("mfa", "RATE_LIMIT_MFA")),
) -> Any:
    """DEMO_MODE helper: current HOD code for a valid MFA challenge."""
    return auth_service.demo_code_for_challenge(
        db, payload.mfa_token, request.client.host if request.client else "unknown"
    )


@router.post("/mfa/demo-code", response_model=DemoCodeResponse)
def mfa_demo_code(
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    _: None = Depends(rate_limit("mfa", "RATE_LIMIT_MFA")),
) -> Any:
    return auth_service.demo_code(db, principal.user, principal.ip)
