"""Paper encryption service (AES-256-GCM envelope encryption).

Design
------
* Every paper gets a fresh 256-bit data encryption key (DEK).
* The DEK is wrapped with a master key (KEK) held outside the database:
  environment variables in development, a KMS/HSM in production.
* The database stores only: key id, wrapped DEK, payload nonce, SHA-256 hashes
  and the storage path of the ciphertext blob. No plaintext is persisted.
* `key_id` supports rotation: new writes always use the active key while old
  ciphertexts can still be unwrapped with the previous key.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.security import sha256_hex


class KeyManagementError(RuntimeError):
    pass


@dataclass(frozen=True)
class WrappedKey:
    key_id: str
    wrapped_dek: str


class KeyManager:
    """Envelope key manager.

    DEV: master keys come from `MASTER_KEY` / `MASTER_KEYS_JSON` env vars.
    PROD: replace this class with a KMS adapter (AWS KMS, GCP KMS, Vault). The
    rest of the application depends only on wrap()/unwrap() and key ids, so the
    swap does not touch paper, release or storage logic.
    """

    def __init__(self) -> None:
        self._keys = settings.master_keys
        self._active = settings.MASTER_KEY_ID
        if self._active not in self._keys:
            raise KeyManagementError(
                f"Active key '{self._active}' missing from key material; set MASTER_KEYS_JSON"
            )

    @property
    def active_key_id(self) -> str:
        return self._active

    def known_key_ids(self) -> list[str]:
        return sorted(self._keys)

    @staticmethod
    def _aad(paper_id: str) -> bytes:
        return f"praxis-paper-dek::{paper_id}".encode()

    def generate_dek(self) -> bytes:
        return secrets.token_bytes(32)

    def wrap(self, paper_id: str, dek: bytes) -> WrappedKey:
        key = self._keys[self._active]
        nonce = secrets.token_bytes(12)
        wrapped = nonce + AESGCM(key).encrypt(nonce, dek, self._aad(paper_id))
        return WrappedKey(self._active, base64.b64encode(wrapped).decode())

    def unwrap(self, paper_id: str, key_id: str, wrapped_dek: str) -> bytes:
        key = self._keys.get(key_id)
        if key is None:
            raise KeyManagementError(
                "The master key required for this paper is not loaded. "
                "Restore the previous key id in MASTER_KEYS_JSON to recover it."
            )
        raw = base64.b64decode(wrapped_dek)
        try:
            return AESGCM(key).decrypt(raw[:12], raw[12:], self._aad(paper_id))
        except Exception as exc:  # noqa: BLE001 - normalised to KMS error
            raise KeyManagementError("Paper key unwrap failed: authentication error") from exc


km = KeyManager()


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def paper_aad(paper_id: str, version: int, exam_id: str) -> bytes:
    return f"praxis-paper::{paper_id}::v{version}::exam::{exam_id}".encode()


@dataclass
class EncryptionResult:
    ciphertext: bytes
    key_id: str
    wrapped_dek: str
    payload_nonce: str
    ciphertext_sha256: str
    plaintext_sha256: str
    aad: str
    algorithm: str = "AES-256-GCM"


def encrypt_paper(*, paper_id: str, exam_id: str, version: int, payload: dict[str, Any]) -> EncryptionResult:
    plaintext = canonical_json(payload)
    dek = km.generate_dek()
    wrapped = km.wrap(paper_id, dek)
    aad = paper_aad(paper_id, version, exam_id)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
    # The DEK is deliberately dropped here: it exists only inside this frame.
    return EncryptionResult(
        ciphertext=ciphertext,
        key_id=wrapped.key_id,
        wrapped_dek=wrapped.wrapped_dek,
        payload_nonce=base64.b64encode(nonce).decode(),
        ciphertext_sha256=sha256_hex(ciphertext),
        plaintext_sha256=sha256_hex(plaintext),
        aad=aad.decode(),
    )


def decrypt_paper(
    *,
    paper_id: str,
    exam_id: str,
    version: int,
    ciphertext: bytes,
    key_id: str,
    wrapped_dek: str,
    payload_nonce: str,
    expected_sha256: str,
) -> tuple[dict[str, Any], bool]:
    if sha256_hex(ciphertext) != expected_sha256:
        raise KeyManagementError("Ciphertext integrity check failed before decryption")
    dek = km.unwrap(paper_id, key_id, wrapped_dek)
    nonce = base64.b64decode(payload_nonce)
    plaintext = AESGCM(dek).decrypt(nonce, ciphertext, paper_aad(paper_id, version, exam_id))
    del dek
    try:
        payload = json.loads(plaintext)
    except json.JSONDecodeError as exc:  # pragma: no cover - corruption only
        raise KeyManagementError("Decrypted payload is not valid JSON") from exc
    del plaintext
    return payload, True


def secure_zero_hint() -> str:
    """Documented operational note surfaced in the API/docs."""
    return (
        "Plaintext exists only in request memory while the printable PDF is streamed. "
        "Python byte buffers cannot be reliably zeroed; production deployments should "
        "run decryption in a dedicated short-lived worker/HSM with memory protections."
    )


def key_status() -> dict[str, Any]:
    return {
        "active_key_id": km.active_key_id,
        "known_key_ids": km.known_key_ids(),
        "algorithm": settings.PAPER_CIPHER,
        "backend": "env-vars" if settings.ENV != "production" else "external-kms-recommended",
        "rotation_supported": True,
        "env": settings.ENV,
        "pid": os.getpid(),
    }
