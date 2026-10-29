"""Structured application errors and safe HTTP handlers.

Handlers never leak stack traces, SQL or internal paths to the client.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("praxis.errors")


class AppError(Exception):
    """Business error carrying a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class Unauthorized(AppError):
    def __init__(self, message: str = "Authentication is required.", code: str = "UNAUTHORIZED"):
        super().__init__(code, message, status.HTTP_401_UNAUTHORIZED)


class Forbidden(AppError):
    def __init__(self, message: str = "You are not authorized to perform this action.", code: str = "FORBIDDEN"):
        super().__init__(code, message, status.HTTP_403_FORBIDDEN)


class NotFound(AppError):
    def __init__(self, resource: str = "Resource"):
        super().__init__("NOT_FOUND", f"{resource} was not found.", status.HTTP_404_NOT_FOUND)


class Conflict(AppError):
    def __init__(self, message: str, code: str = "CONFLICT"):
        super().__init__(code, message, status.HTTP_409_CONFLICT)


class RateLimited(AppError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "RATE_LIMITED",
            "Too many requests. Please slow down and try again.",
            status.HTTP_429_TOO_MANY_REQUESTS,
            {"retry_after_seconds": retry_after},
        )


class InvalidState(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_STATE_TRANSITION", message, status.HTTP_409_CONFLICT)


def _body(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": code, "message": message}
    if details is not None:
        payload["details"] = details
    return payload


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.exception("application error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.details),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {
                "field": ".".join(str(part) for part in err.get("loc", [])[1:]) or "body",
                "message": err.get("msg", "Invalid value"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_body("VALIDATION_ERROR", "The submitted data is invalid.", problems),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            429: "RATE_LIMITED",
        }
        message = str(exc.detail) if exc.detail else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(codes.get(exc.status_code, "REQUEST_FAILED"), message),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content=_body("INTERNAL_ERROR", "An unexpected error occurred. The incident has been logged."),
        )
