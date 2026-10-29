"""Append-only audit trail and security event inspection (HOD only)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import Principal, require_hod
from app.db.session import get_db
from app.models.tables import AuditLog, SecurityEvent
from app.schemas.exam import AuditLogOut
from app.services import audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs")
def audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event: str | None = Query(default=None, max_length=80),
    success: bool | None = Query(default=None),
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = select(AuditLog)
    count_query = select(func.count(AuditLog.seq))
    if event:
        query = query.where(AuditLog.event == event)
        count_query = count_query.where(AuditLog.event == event)
    if success is not None:
        query = query.where(AuditLog.success.is_(success))
        count_query = count_query.where(AuditLog.success.is_(success))
    rows = db.execute(query.order_by(AuditLog.seq.desc()).limit(limit).offset(offset)).scalars().all()
    total = int(db.execute(count_query).scalar_one())
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [AuditLogOut.model_validate(row, from_attributes=True).model_dump() for row in rows],
    }


@router.get("/verify")
def verify(
    limit: int = Query(default=5000, ge=1, le=20000),
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return audit_service.verify_chain(db, limit=limit)


@router.get("/summary")
def summary(
    days: int = Query(default=7, ge=1, le=90),
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return audit_service.audit_summary(db, days=days)


@router.get("/security-events")
def security_events(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_hod),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        db.execute(select(SecurityEvent).order_by(SecurityEvent.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "severity": row.severity,
            "summary": row.summary,
            "actor_id": row.actor_id,
            "ip_address": row.ip_address,
            "detail": row.detail,
            "created_at": row.created_at,
        }
        for row in rows
    ]
