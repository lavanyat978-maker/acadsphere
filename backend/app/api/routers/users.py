"""Account administration (HOD only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import Principal, require_hod
from app.db.session import get_db
from app.models.enums import Role
from app.schemas.users import (
    AdminUserOut,
    PasswordResetOut,
    UserCreate,
    UserCreatedOut,
    UserUpdate,
)
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[AdminUserOut])
def list_users(
    role: Role | None = Query(default=None),
    q: str | None = Query(default=None, max_length=120),
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> list[dict]:
    return user_service.list_users(db, role=role, q=q)


@router.post("", response_model=UserCreatedOut, status_code=201)
def create_user(
    payload: UserCreate,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict:
    user, temporary = user_service.create_user(db, payload, actor=principal.user, ip=principal.ip)
    db.commit()
    return {"user": user_service.user_out(db, user), "temporary_password": temporary}


@router.post("/import", status_code=201)
async def import_users(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict:
    """Bulk-create accounts from CSV. Validates every row first; all or nothing."""
    from starlette.concurrency import run_in_threadpool

    data = await file.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        from app.core.errors import AppError

        raise AppError("CSV_TOO_LARGE", "The CSV file must be under 2 MB.", 400)

    def work() -> list[dict]:
        rows = user_service.import_users_csv(db, principal.user, data, principal.ip)
        db.commit()
        return rows

    created = await run_in_threadpool(work)
    return {"created": created, "count": len(created)}


@router.patch("/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict:
    user = user_service.update_user(db, principal.user, user_id, payload, principal.ip)
    db.commit()
    return user_service.user_out(db, user)


@router.post("/{user_id}/reset-password", response_model=PasswordResetOut)
def reset_password(
    user_id: str,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict:
    temporary = user_service.reset_password(db, principal.user, user_id, principal.ip)
    db.commit()
    return {"user_id": user_id, "temporary_password": temporary}


@router.post("/{user_id}/reset-mfa", response_model=AdminUserOut)
def reset_mfa(
    user_id: str,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict:
    user = user_service.reset_mfa(db, principal.user, user_id, principal.ip)
    db.commit()
    return user_service.user_out(db, user)


@router.post("/{user_id}/unlock", response_model=AdminUserOut)
def unlock(
    user_id: str,
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict:
    user = user_service.unlock(db, principal.user, user_id, principal.ip)
    db.commit()
    return user_service.user_out(db, user)
