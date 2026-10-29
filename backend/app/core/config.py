"""Application configuration.

All secrets are read from the environment. In production the application refuses
to boot with development defaults, which prevents the classic "demo settings
shipped to prod" failure mode.
"""
from __future__ import annotations

import base64
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = BASE_DIR / "var" / "app.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    APP_NAME: str = "Praxis Secure Assessment Platform"
    APP_VERSION: str = "1.0.0"
    ENV: Literal["development", "test", "production"] = "development"
    API_PREFIX: str = "/api"
    DEBUG: bool = False
    DEMO_MODE: bool = True

    # --- Auth -------------------------------------------------------------
    JWT_SECRET: str = "dev-only-insecure-jwt-secret-change-me-32chars"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 60
    MFA_CHALLENGE_TTL_MINUTES: int = 5
    PASSWORD_MIN_LENGTH: int = 10
    MAX_FAILED_LOGINS: int = 5
    LOGIN_LOCK_MINUTES: int = 15
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"

    # --- Encryption / key management --------------------------------------
    MASTER_KEY_ID: str = "dev-key-1"
    MASTER_KEY: str = ""  # base64 encoded 32 bytes; generated for dev if empty
    MASTER_KEYS_JSON: str = ""  # {"key-id": "<base64 32 bytes>"} for rotation
    PAPER_CIPHER: str = "AES-256-GCM"

    # --- Database ---------------------------------------------------------
    DATABASE_URL: str = f"sqlite:///{DEFAULT_SQLITE_PATH}"
    DB_ECHO: bool = False

    # --- CORS -------------------------------------------------------------
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )

    # --- AI ---------------------------------------------------------------
    LLM_PROVIDER: Literal["auto", "openai", "mock"] = "auto"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TIMEOUT_SECONDS: int = 60
    EMBEDDING_PROVIDER: Literal["auto", "openai", "local"] = "auto"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 256
    AI_MAX_RETRIES: int = 3
    AI_TEMPERATURE: float = 0.4

    # --- Storage ----------------------------------------------------------
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_PATH: str = str(BASE_DIR / "var" / "storage")
    S3_BUCKET: str = ""
    S3_PREFIX: str = "praxis"
    S3_ENDPOINT_URL: str = ""
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""

    # --- Uploads ----------------------------------------------------------
    MAX_UPLOAD_MB: int = 15
    ALLOWED_UPLOAD_EXTENSIONS: list[str] = ["pdf", "docx", "txt"]

    # --- Rate limits (per minute unless stated) ---------------------------
    RATE_LIMIT_LOGIN: int = 8
    RATE_LIMIT_MFA: int = 8
    RATE_LIMIT_GENERAL: int = 240
    RATE_LIMIT_GENERATION: int = 10

    # --- Generation / release --------------------------------------------
    GEN_INLINE: bool = True  # run the generation job in-process (dev)
    GEN_POLL_SECONDS: int = 5
    RELEASE_WINDOW_MINUTES: int = 120
    RELEASE_TOKEN_TTL_MINUTES: int = 10
    PRINT_EARLY_OPEN_MINUTES: int = 1440
    SEED_ON_STARTUP: bool = False

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @field_validator("ALLOWED_UPLOAD_EXTENSIONS", mode="before")
    @classmethod
    def split_ext(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip().lower() for part in value.split(",") if part.strip()]
        return value

    # --- Derived helpers ---------------------------------------------------
    @property
    def master_keys(self) -> dict[str, bytes]:
        keys: dict[str, bytes] = {}
        if self.MASTER_KEYS_JSON:
            for key_id, encoded in json.loads(self.MASTER_KEYS_JSON).items():
                keys[key_id] = base64.b64decode(encoded)
        if self.MASTER_KEY:
            keys[self.MASTER_KEY_ID] = base64.b64decode(self.MASTER_KEY)
        if not keys:
            # Deterministic development key derived from the app secret so that
            # restarts can still read previously encrypted demo papers.
            import hashlib

            keys[self.MASTER_KEY_ID] = hashlib.sha256(
                f"dev-master::{self.JWT_SECRET}".encode()
            ).digest()
        for key_id, material in keys.items():
            if len(material) != 32:
                raise ValueError(f"Master key '{key_id}' must decode to 32 bytes")
        return keys

    @property
    def ai_mode(self) -> str:
        return "REAL_AI" if self.effective_llm_provider == "openai" else "DEMO"

    @property
    def effective_llm_provider(self) -> str:
        if self.LLM_PROVIDER == "auto":
            return "openai" if self.LLM_API_KEY else "mock"
        return self.LLM_PROVIDER

    @property
    def effective_embedding_provider(self) -> str:
        if self.EMBEDDING_PROVIDER == "auto":
            return "openai" if self.LLM_API_KEY else "local"
        return self.EMBEDDING_PROVIDER

    def validate_for_production(self) -> None:
        if self.ENV != "production":
            return
        problems: list[str] = []
        if "dev-only" in self.JWT_SECRET or len(self.JWT_SECRET) < 32:
            problems.append("JWT_SECRET must be a strong secret of at least 32 characters")
        if not self.MASTER_KEY and not self.MASTER_KEYS_JSON:
            problems.append("MASTER_KEY (base64, 32 bytes) or MASTER_KEYS_JSON must be set")
        if self.DEMO_MODE:
            problems.append("DEMO_MODE must be false in production")
        if self.effective_llm_provider == "mock":
            problems.append("A real LLM provider must be configured in production")
        if not self.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true in production")
        if self.DEBUG:
            problems.append("DEBUG must be false in production")
        if problems:
            raise RuntimeError("Insecure production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Managed Postgres providers (Render, Fly, Railway, Heroku) hand out URLs as
    # `postgres://user:pass@host/db`; SQLAlchemy needs its explicit psycopg URL.
    if settings.DATABASE_URL.startswith("postgres://"):
        object.__setattr__(
            settings,
            "DATABASE_URL",
            settings.DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1),
        )
    if settings.ENV == "production":
        settings.validate_for_production()
    if settings.DATABASE_URL.startswith("sqlite"):
        Path(settings.STORAGE_LOCAL_PATH).mkdir(parents=True, exist_ok=True)
        db_path = settings.DATABASE_URL.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
os.environ.setdefault("TZ", "UTC")
