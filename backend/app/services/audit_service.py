"""Tamper-evident audit logging.

Each record includes the hash of the previous record, so any edit or deletion in
the middle of the chain breaks verification. Metadata is sanitised: secrets and
plaintext paper content must never reach this layer.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import Severity
from app.models.tables import AuditLog, SecurityEvent, User

FORBIDDEN_METADATA_KEYS = {
    "password",
    "current_password",
    "new_password",
    "password_hash",
    "token",
    "access_token",
    "release_token",
    "mfa_code",
    "mfa_secret",
    "secret",
    "api_key",
    "master_key",
    "wrapped_dek",
    "question_text",
    "questions",
    "paper",
    "ciphertext",
    "plaintext",
}


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_METADATA_KEYS:
                clean[key] = "[redacted]"
            else:
                clean[key] = _sanitize(item, depth + 1)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth + 1) for item in value[:20]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


def _canonical_time(value: datetime) -> str:
    """Naive UTC string so the hash survives SQLite's tz-naive round-trip."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


def _hash_record(prev_hash: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{prev_hash}|{canonical}".encode()).hexdigest()


def record_audit(
    db: Session,
    *,
    event: str,
    actor: User | None = None,
    actor_role: str | None = None,
    resource_type: str = "",
    resource_id: str = "",
    success: bool = True,
    ip: str = "",
    user_agent: str = "",
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> AuditLog:
    # Bound every column to its schema size. PostgreSQL rejects overlong values
    # (SQLite silently stores them), and a failed audit write must never turn a
    # denial into a 500. The hashed payload uses the same bounded values.
    event = event[:80]
    resource_type = (resource_type or "")[:60]
    resource_id = (resource_id or "")[:60]
    ip = (ip or "")[:64]
    last = db.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)).scalar_one_or_none()
    prev_hash = last.record_hash if last else "GENESIS"
    created_at = datetime.now(timezone.utc)
    payload = {
        "event": event,
        "actor_id": actor.id if actor else None,
        "actor_email": actor.email if actor else "",
        "actor_role": actor.role if actor else (actor_role or "ANONYMOUS"),
        "resource_type": resource_type,
        "resource_id": resource_id,
        "success": bool(success),
        "created_at": _canonical_time(created_at),
        "metadata": _sanitize(metadata or {}),
    }
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else "",
        actor_role=actor.role if actor else (actor_role or "ANONYMOUS"),
        event=event,
        resource_type=resource_type,
        resource_id=resource_id,
        success=bool(success),
        ip_address=ip,
        user_agent=user_agent[:255],
        metadata_json=_sanitize(metadata or {}),
        prev_hash=prev_hash,
        record_hash=_hash_record(prev_hash, payload),
        created_at=created_at,
    )
    db.add(entry)
    db.flush()
    if commit:
        db.commit()
    return entry


def record_security_event(
    db: Session,
    *,
    event_type: str,
    summary: str,
    severity: Severity | str = Severity.INFO,
    actor_id: str | None = None,
    ip: str = "",
    detail: dict[str, Any] | None = None,
) -> SecurityEvent:
    event = SecurityEvent(
        event_type=event_type[:80],
        severity=str(severity),
        summary=summary[:255],
        actor_id=actor_id,
        ip_address=(ip or "")[:64],
        detail=_sanitize(detail or {}),
    )
    db.add(event)
    db.flush()
    return event


def verify_chain(db: Session, limit: int = 5000) -> dict[str, Any]:
    rows = list(db.execute(select(AuditLog).order_by(AuditLog.seq.asc()).limit(limit)).scalars())
    prev = "GENESIS"
    broken_at: int | None = None
    for row in rows:
        payload = {
            "event": row.event,
            "actor_id": row.actor_id,
            "actor_email": row.actor_email,
            "actor_role": row.actor_role,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "success": row.success,
            "created_at": _canonical_time(row.created_at),
            "metadata": row.metadata_json or {},
        }
        expected = _hash_record(prev, payload)
        if row.prev_hash != prev or row.record_hash != expected:
            broken_at = row.seq
            break
        prev = row.record_hash
    return {
        "verified": broken_at is None,
        "records_checked": len(rows),
        "broken_at_seq": broken_at,
        "head_hash": prev,
    }


def audit_summary(db: Session, days: int = 7) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    failed = db.execute(
        select(AuditLog).where(AuditLog.created_at >= since, AuditLog.success.is_(False))
    ).scalars().all()
    total = db.execute(select(AuditLog).where(AuditLog.created_at >= since)).scalars().all()
    events: dict[str, int] = {}
    for row in total:
        events[row.event] = events.get(row.event, 0) + 1
    return {
        "window_days": days,
        "total_events": len(total),
        "denied_events": len(failed),
        "top_events": sorted(events.items(), key=lambda kv: kv[1], reverse=True)[:10],
    }
