"""Structured logging, rate limiting, CSRF and secure-header middleware."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.security import constant_time_equal

SENSITIVE_KEYS = {
    "password",
    "current_password",
    "new_password",
    "token",
    "access_token",
    "authorization",
    "mfa_code",
    "secret",
    "api_key",
    "encryption_key",
    "release_token",
    "question_text",
    "ciphertext",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "user_id", "role", "path", "method", "status", "duration_ms", "event"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("praxis").setLevel(logging.INFO)


class RateLimiter:
    """Single-node sliding-window limiter.

    Deliberately simple: the platform is a modular monolith deployed as one
    process. `REDIS_URL` is reserved in .env for a shared-store upgrade.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = int(window_seconds - (now - bucket[0])) + 1
                return False, max(retry, 1)
            bucket.append(now)
            if len(self._hits) > 20000:  # bound memory
                for stale in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                    self._hits.pop(stale, None)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.started_at = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if settings.ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith(settings.API_PREFIX):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        logger = logging.getLogger("praxis.access")
        response = await call_next(request)
        duration = (time.perf_counter() - getattr(request.state, "started_at", time.perf_counter())) * 1000
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration, 2),
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return response


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT_PATHS = {
    f"{settings.API_PREFIX}/auth/login",
    f"{settings.API_PREFIX}/auth/mfa/verify",
    f"{settings.API_PREFIX}/auth/logout",
}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF protection for cookie-authenticated requests.

    Requests authenticated with an explicit `Authorization: Bearer` header are
    not CSRF-exposed and therefore exempt.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if (
            request.method in UNSAFE_METHODS
            and request.url.path.startswith(settings.API_PREFIX)
            and request.url.path not in CSRF_EXEMPT_PATHS
            and request.cookies.get("access_token")
            and not request.headers.get("authorization")
        ):
            cookie_token = request.cookies.get("csrf_token", "")
            header_token = request.headers.get("x-csrf-token", "")
            if not cookie_token or not header_token or not constant_time_equal(cookie_token, header_token):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "CSRF_FAILED",
                        "message": "Missing or invalid CSRF token for this session.",
                    },
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)
