"""Syllabus document service: validation, storage, RAG ingestion, retrieval."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.security import sha256_hex
from app.models.tables import SyllabusDocument, User
from app.rag.pipeline import (
    DocumentError,
    ingest_document,
    retrieve,
    store_document_file,
)
from app.services.academic_service import ensure_subject_access
from app.services.audit_service import record_audit
from app.storage.backend import StorageError, get_storage


def _validate_upload(filename: str, content_type: str, data: bytes) -> str:
    if not data:
        raise DocumentError("The uploaded file is empty.")
    size_limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > size_limit:
        raise DocumentError(f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit.")
    if "\x00" in filename:
        raise DocumentError("Invalid file name.")
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise DocumentError(
            f"Unsupported file type. Allowed: {', '.join(settings.ALLOWED_UPLOAD_EXTENSIONS)}."
        )
    if suffix == "pdf" and not data.startswith(b"%PDF"):
        raise DocumentError("The file does not look like a valid PDF.")
    if suffix == "docx" and not data.startswith(b"PK"):
        raise DocumentError("The file does not look like a valid DOCX archive.")
    if suffix == "txt":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                data.decode("latin-1")
            except UnicodeDecodeError as exc:
                raise DocumentError("Text file is not readable text.") from exc
    return suffix


def upload(
    db: Session,
    *,
    actor: User,
    subject_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    ip: str = "",
    user_agent: str = "",
) -> SyllabusDocument:
    subject = ensure_subject_access(db, actor, subject_id)
    _validate_upload(filename, content_type, data)
    checksum = sha256_hex(data)
    duplicate = db.execute(
        select(SyllabusDocument).where(
            SyllabusDocument.subject_id == subject.id, SyllabusDocument.checksum == checksum
        )
    ).scalar_one_or_none()
    if duplicate:
        raise Conflict("This document was already uploaded for the subject.", "DUPLICATE_DOCUMENT")

    document = SyllabusDocument(
        subject_id=subject.id,
        uploaded_by=actor.id,
        original_name=filename[:255],
        content_type=(content_type or "application/octet-stream")[:120],
        size_bytes=len(data),
        checksum=checksum,
        status="PROCESSING",
        storage_path="",
    )
    db.add(document)
    db.flush()
    stored_key = ""
    try:
        stored_key = store_document_file(document, filename, data)
        document.storage_path = stored_key
        ingest_document(db, subject=subject, document=document, data=data)
    except (DocumentError, StorageError) as exc:
        db.rollback()
        if stored_key:
            try:
                get_storage().delete(stored_key)
            except StorageError:
                pass
        if isinstance(exc, AppError):
            raise
        raise DocumentError(str(exc)) from exc
    except Exception:
        db.rollback()
        if stored_key:
            try:
                get_storage().delete(stored_key)
            except StorageError:
                pass
        raise
    record_audit(
        db,
        event="SYLLABUS_UPLOADED",
        actor=actor,
        resource_type="syllabus_document",
        resource_id=document.id,
        ip=ip,
        user_agent=user_agent,
        metadata={
            "subject_id": subject.id,
            "filename": filename,
            "size_bytes": len(data),
            "chunks": document.chunk_count,
            "checksum": checksum[:12],
        },
    )
    db.commit()
    return document


def list_documents(db: Session, actor: User, subject_id: str) -> list[dict]:
    ensure_subject_access(db, actor, subject_id)
    rows = db.execute(
        select(SyllabusDocument)
        .where(SyllabusDocument.subject_id == subject_id)
        .order_by(SyllabusDocument.created_at.desc())
    ).scalars().all()
    names = {
        user.id: user.full_name
        for user in db.execute(
            select(User).where(User.id.in_([row.uploaded_by for row in rows]))
        ).scalars()
    } if rows else {}
    return [
        {
            "id": row.id,
            "subject_id": row.subject_id,
            "original_name": row.original_name,
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "chunk_count": row.chunk_count,
            "char_count": row.char_count,
            "status": row.status,
            "created_at": row.created_at,
            "uploaded_by_name": names.get(row.uploaded_by, ""),
        }
        for row in rows
    ]


def delete_document(db: Session, actor: User, document_id: str, ip: str = "") -> None:
    document = db.get(SyllabusDocument, document_id)
    if document is None:
        raise NotFound("Document")
    ensure_subject_access(db, actor, document.subject_id)
    if document.uploaded_by != actor.id and actor.role != "HOD":
        raise Forbidden("Only the uploader or HOD can delete this document.")
    path = document.storage_path
    name = document.original_name
    db.delete(document)
    record_audit(
        db,
        event="SYLLABUS_DELETED",
        actor=actor,
        resource_type="syllabus_document",
        resource_id=document_id,
        ip=ip,
        metadata={"filename": name},
    )
    db.commit()
    if path:
        try:
            get_storage().delete(path)
        except Exception:  # noqa: BLE001 - the record is gone; an orphaned blob is only a cleanup task
            pass


def search(db: Session, actor: User, subject_id: str, query: str, top_k: int = 5) -> list[dict]:
    ensure_subject_access(db, actor, subject_id)
    if not query.strip():
        raise DocumentError("Provide a search query.")
    return retrieve(db, subject_id=subject_id, query=query, top_k=min(top_k, 10))
