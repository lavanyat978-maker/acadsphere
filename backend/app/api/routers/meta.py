"""Health, runtime metadata and demo helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.provider import provider_status
from app.core.config import settings
from app.core.demo import DEMO_ACCOUNTS
from app.db.session import get_db
from app.encryption.service import key_status

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    database = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"
    status = "ok" if database == "ok" else "degraded"
    return {
        "status": status,
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "revision": settings.GIT_SHA,
        "env": settings.ENV,
        "database": database,
        "ai_mode": settings.ai_mode,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/meta")
def meta() -> dict[str, Any]:
    return {
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.ENV,
        "demo_mode": settings.DEMO_MODE,
        "ai": provider_status(),
        "encryption": key_status(),
        "upload_limits": {
            "max_mb": settings.MAX_UPLOAD_MB,
            "extensions": settings.ALLOWED_UPLOAD_EXTENSIONS,
        },
        "release_window_minutes": settings.RELEASE_WINDOW_MINUTES,
        "print_limit": 3,
        "demo_accounts": DEMO_ACCOUNTS if settings.DEMO_MODE and settings.ENV != "production" else [],
    }
