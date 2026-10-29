"""Object storage abstraction.

Only ciphertext for internal papers and raw syllabus documents use this layer.
The local backend is the development default; the S3 backend talks to any
S3-compatible endpoint (AWS, MinIO, R2) and is selected via env configuration.
"""
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings


class StorageError(RuntimeError):
    pass


class StorageBackend(ABC):
    name: str

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalStorage(StorageBackend):
    name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        target = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in target.parents and target != root:
            raise StorageError("Invalid storage key")
        return target

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
        return key

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise StorageError("Stored object not found")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def clear(self) -> None:  # pragma: no cover - test helper
        if self.root.exists():
            shutil.rmtree(self.root)


class S3Storage(StorageBackend):
    name = "s3"

    def __init__(self) -> None:
        try:
            import boto3  # type: ignore
            from botocore.config import Config  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise StorageError(
                "S3 storage selected but boto3 is not installed. "
                "Install it with `pip install 'praxis-backend[s3]'`."
            ) from exc
        if not settings.S3_BUCKET:
            raise StorageError("S3_BUCKET must be configured when STORAGE_BACKEND=s3")
        self._bucket = settings.S3_BUCKET
        self._client = boto3.client(
            "s3",
            region_name=settings.S3_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL or None,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY or None,
            config=Config(signature_version="s3v4", retries={"max_attempts": 4}),
        )

    def _key(self, key: str) -> str:
        prefix = settings.S3_PREFIX.strip("/")
        return f"{prefix}/{key}" if prefix else key

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._key(key),
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="AES256",
            Metadata={"platform": "praxis"},
        )
        return key

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(key))
        except Exception as exc:  # noqa: BLE001
            raise StorageError("Stored object could not be read") from exc
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._key(key))

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._key(key))
            return True
        except Exception:  # noqa: BLE001
            return False


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        if settings.STORAGE_BACKEND == "s3":
            _backend = S3Storage()
        else:
            _backend = LocalStorage(settings.STORAGE_LOCAL_PATH)
    return _backend


def set_storage(backend: StorageBackend) -> None:  # test helper
    global _backend
    _backend = backend


def paper_key(exam_id: str, paper_id: str, version: int) -> str:
    return f"papers/{exam_id}/{paper_id}-v{version}.enc"


def syllabus_key(subject_id: str, document_id: str, filename: str) -> str:
    safe = "".join(char for char in filename if char.isalnum() or char in "._-")[-60:]
    return f"syllabus/{subject_id}/{document_id}-{safe}"
