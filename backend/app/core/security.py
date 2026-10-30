"""Security primitives: password hashing, JWTs, TOTP secret sealing, CSRF."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
_DUMMY_HASH: str | None = None


def password_policy_problem(password: str) -> str | None:
    """Return why a password is too weak, or None when it is acceptable."""
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters"
    classes = sum(
        [
            any(char.islower() for char in password),
            any(char.isupper() for char in password),
            any(char.isdigit() for char in password),
            any(not char.isalnum() for char in password),
        ]
    )
    if classes < 3:
        return "Password must mix at least three of: lower, upper, digit, symbol"
    return None


def generate_temporary_password() -> str:
    """A policy-compliant one-time password handed to a user by the HOD."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{body[:4]}-{body[4:8]}-{body[8:]}#{secrets.randbelow(90) + 10}"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def burn_password_check(password: str) -> None:
    """Constant-ish work for unknown accounts to blunt user enumeration."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("timing-equalizer-placeholder")
    verify_password(password, _DUMMY_HASH)


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:  # pragma: no cover - defensive
        return True


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


TokenKind = Literal["access", "mfa_challenge", "mfa_enroll"]


def create_token(
    user_id: str,
    role: str,
    kind: TokenKind = "access",
    mfa_verified: bool = False,
    token_version: int = 0,
    ttl_minutes: int | None = None,
) -> tuple[str, datetime]:
    if kind == "access":
        ttl = ttl_minutes or settings.ACCESS_TOKEN_TTL_MINUTES
    else:
        ttl = ttl_minutes or settings.MFA_CHALLENGE_TTL_MINUTES
    expires_at = utcnow() + timedelta(minutes=ttl)
    payload = {
        "sub": user_id,
        "role": role,
        "typ": kind,
        "mfa": bool(mfa_verified),
        "tv": token_version,
        "jti": str(uuid.uuid4()),
        "iat": int(utcnow().timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "praxis",
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, expires_at


def decode_token(token: str, expected: TokenKind = "access") -> dict[str, Any]:
    payload = jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "sub", "typ"]},
    )
    if payload.get("typ") != expected:
        raise jwt.InvalidTokenError("Unexpected token type")
    return payload


# --- MFA secret sealing ---------------------------------------------------
def _sealing_key() -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"praxis-mfa",
        info=b"totp-secret-sealing-v1",
    ).derive(settings.JWT_SECRET.encode())


def seal_secret(plaintext: str) -> str:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_sealing_key()).encrypt(nonce, plaintext.encode(), b"totp")
    return base64.b64encode(nonce + ct).decode()


def open_secret(sealed: str) -> str:
    raw = base64.b64decode(sealed)
    return AESGCM(_sealing_key()).decrypt(raw[:12], raw[12:], b"totp").decode()


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Praxis Exams")


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    # valid_window=1 tolerates one 30s step of clock drift.
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def current_totp(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def generate_release_token() -> tuple[str, str]:
    token = random_token(32)
    return token, sha256_hex(token)
