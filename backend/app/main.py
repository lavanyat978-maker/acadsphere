"""Praxis Secure Assessment Platform - FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import academic, analytics, audit, auth, exams, meta, practice, syllabus, users
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.runtime import (
    CSRFMiddleware,
    RequestLogMiddleware,
    SecurityHeadersMiddleware,
    configure_logging,
)
from app.db.session import init_db

logger = logging.getLogger("praxis.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    init_db()
    if settings.GEN_INLINE:
        from app.services.generation_service import recover_interrupted

        recover_interrupted()
    if settings.SEED_ON_STARTUP:
        from app.seed import seed_if_empty

        seed_if_empty()
    logger.info(
        "application started",
        extra={
            "event": "startup",
            "env": settings.ENV,
            "ai_mode": settings.ai_mode,
            "version": settings.APP_VERSION,
        },
    )
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Internal assessment platform with role-based access, adaptive practice, "
        "RAG-grounded question generation, five-level validation, and HOD-controlled "
        "paper release with AES-256-GCM envelope encryption."
    ),
    lifespan=lifespan,
    docs_url="/docs" if settings.ENV != "production" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.ENV != "production" else None,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLogMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Print-Count", "X-Prints-Remaining"],
)

install_error_handlers(app)

api = settings.API_PREFIX
app.include_router(meta.router, prefix=api)
app.include_router(auth.router, prefix=api)
app.include_router(users.router, prefix=api)
app.include_router(academic.router, prefix=api)
app.include_router(syllabus.router, prefix=api)
app.include_router(practice.router, prefix=api)
app.include_router(exams.router, prefix=api)
app.include_router(analytics.router, prefix=api)
app.include_router(audit.router, prefix=api)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "api": f"{api}/health",
        "docs": "/docs" if settings.ENV != "production" else "disabled",
    }
