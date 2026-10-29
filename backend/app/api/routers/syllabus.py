"""Syllabus upload, listing, deletion and RAG search."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal, require_staff
from app.core.errors import AppError
from app.db.session import get_db
from app.schemas.academic import RagSearchResult, SyllabusDocumentOut
from app.services import syllabus_service

router = APIRouter(prefix="/syllabus", tags=["syllabus"])


def _document_out(document) -> dict:
    return {
        "id": document.id,
        "subject_id": document.subject_id,
        "original_name": document.original_name,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "chunk_count": document.chunk_count,
        "char_count": document.char_count,
        "status": document.status,
        "created_at": document.created_at,
        "uploaded_by_name": "",
    }


@router.post("/documents", response_model=SyllabusDocumentOut, status_code=201)
async def upload_document(
    subject_id: str = Form(...),
    file: UploadFile = File(...),
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    data = await file.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    document = syllabus_service.upload(
        db,
        actor=principal.user,
        subject_id=subject_id,
        filename=file.filename or "syllabus.txt",
        content_type=file.content_type or "application/octet-stream",
        data=data,
        ip=principal.ip,
        user_agent=principal.user_agent,
    )
    return _document_out(document)


@router.get("/documents", response_model=list[SyllabusDocumentOut])
def list_documents(
    subject_id: str = Query(...),
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> list[dict]:
    return syllabus_service.list_documents(db, principal.user, subject_id)


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    syllabus_service.delete_document(db, principal.user, document_id, principal.ip)
    return {"deleted": True}


@router.get("/search", response_model=list[RagSearchResult])
def search_documents(
    subject_id: str = Query(...),
    q: str = Query(min_length=2, max_length=300),
    top_k: int = Query(default=5, ge=1, le=10),
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        return syllabus_service.search(db, principal.user, subject_id, q, top_k)
    except Exception as exc:  # DocumentError -> 400
        from app.rag.pipeline import DocumentError

        if isinstance(exc, DocumentError):
            raise AppError("RAG_SEARCH_FAILED", str(exc), 400) from exc
        raise
