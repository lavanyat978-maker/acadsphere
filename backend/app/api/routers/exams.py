"""Internal examination configuration, secure generation and HOD release."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal, require_hod, require_mfa, require_staff
from app.db.session import get_db
from app.schemas.exam import (
    AuthzReleaseOut,
    AuthorizeReleaseRequest,
    CancelExamRequest,
    DecryptOut,
    DecryptRequest,
    ExamConfigCreate,
    ExamOut,
    GenerationStatusOut,
    PrintOut,
    ReleaseHistoryItem,
    ReleaseQueueItem,
    SecurePaperOut,
)
from app.services import exam_service, generation_service

router = APIRouter(prefix="/exams", tags=["exams"])


# --- HOD release workflow -------------------------------------------------
@router.get("/release/queue", response_model=list[ReleaseQueueItem])
def release_queue(
    principal: Principal = Depends(require_hod), db: Session = Depends(get_db)
) -> list[dict]:
    return exam_service.release_queue(db, principal.user)


@router.get("/release/history", response_model=list[ReleaseHistoryItem])
def release_history(
    principal: Principal = Depends(require_hod), db: Session = Depends(get_db)
) -> list[dict]:
    return exam_service.release_history(db, principal.user)


@router.post("/release/{paper_id}/authorize", response_model=AuthzReleaseOut)
def authorize_release(
    paper_id: str,
    payload: AuthorizeReleaseRequest,
    principal: Principal = Depends(require_hod),
    _mfa: Principal = Depends(require_mfa),
    db: Session = Depends(get_db),
) -> dict:
    return exam_service.authorize_release(
        db, principal.user, paper_id, payload.reason, principal.ip
    )


@router.post("/release/{paper_id}/decrypt", response_model=DecryptOut)
def decrypt_release(
    paper_id: str,
    payload: DecryptRequest,
    principal: Principal = Depends(require_hod),
    _mfa: Principal = Depends(require_mfa),
    db: Session = Depends(get_db),
) -> dict:
    return exam_service.decrypt_for_release(
        db, principal.user, paper_id, payload.release_token, payload.reason, principal.ip
    )


@router.get("/release/{paper_id}/pdf")
def release_pdf(
    paper_id: str,
    release_token: str = Query(min_length=20, max_length=200),
    principal: Principal = Depends(require_hod),
    _mfa: Principal = Depends(require_mfa),
    db: Session = Depends(get_db),
) -> Response:
    content, meta = exam_service.printable_pdf(
        db, principal.user, paper_id, release_token, principal.ip
    )
    filename = f"praxis-paper-{meta['paper_id'][:8]}-print{meta['print_count']}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Print-Count": str(meta["print_count"]),
            "X-Prints-Remaining": str(meta["prints_remaining"]),
        },
    )


@router.post("/release/{paper_id}/released")
def mark_released(
    paper_id: str,
    principal: Principal = Depends(require_hod),
    _mfa: Principal = Depends(require_mfa),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return exam_service.mark_released(db, principal.user, paper_id, principal.ip)


@router.get("/scheduled")
def scheduled(
    principal: Principal = Depends(require_hod), db: Session = Depends(get_db)
) -> list[dict]:
    return exam_service.scheduled_exams(db, principal.user)


# --- Paper metadata (never plaintext) ------------------------------------
@router.get("/papers/{paper_id}", response_model=SecurePaperOut)
def paper_metadata(
    paper_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    return exam_service.secure_paper_for_teacher(db, principal.user, paper_id)


@router.get("/papers/{paper_id}/integrity")
def paper_integrity(
    paper_id: str,
    principal: Principal = Depends(require_hod),
    _mfa: Principal = Depends(require_mfa),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return exam_service.integrity_check(db, principal.user, paper_id, principal.ip)


@router.get("/papers/{paper_id}/plaintext")
def deny_plaintext(
    paper_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> None:
    """Explicitly exists to demonstrate the sealed-paper policy."""
    from app.services import paper_service

    paper_service.deny_plaintext_access(
        db, actor=principal.user, resource="question_paper", ip=principal.ip, resource_id=paper_id
    )


# --- Exam configuration ---------------------------------------------------
@router.post("", response_model=ExamOut, status_code=201)
def create_exam(
    payload: ExamConfigCreate,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    exam = exam_service.create_exam(db, principal.user, payload)
    db.commit()
    return exam_service.get_exam(db, principal.user, exam.id)


@router.get("", response_model=list[ExamOut])
def list_exams(
    principal: Principal = Depends(require_staff), db: Session = Depends(get_db)
) -> list[dict]:
    return exam_service.list_exams(db, principal.user)


@router.get("/{exam_id}", response_model=ExamOut)
def get_exam(
    exam_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    return exam_service.get_exam(db, principal.user, exam_id)


@router.put("/{exam_id}", response_model=ExamOut)
def update_exam(
    exam_id: str,
    payload: ExamConfigCreate,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    exam_service.update_exam(db, principal.user, exam_id, payload, principal.ip)
    db.commit()
    return exam_service.get_exam(db, principal.user, exam_id)


@router.post("/{exam_id}/cancel", response_model=ExamOut)
def cancel_exam(
    exam_id: str,
    payload: CancelExamRequest,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    exam_service.cancel_exam(db, principal.user, exam_id, payload.reason, principal.ip)
    db.commit()
    return exam_service.get_exam(db, principal.user, exam_id)


@router.post("/{exam_id}/paper", response_model=ExamOut, status_code=201)
async def upload_paper(
    exam_id: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    """Upload (or replace, until the release window opens) a prepared PDF paper."""
    from starlette.concurrency import run_in_threadpool

    data = await file.read(settings.MAX_PAPER_UPLOAD_MB * 1024 * 1024 + 1)

    def work() -> dict:
        exam_service.upload_paper(
            db, principal.user, exam_id, filename=file.filename or "paper.pdf", data=data, ip=principal.ip
        )
        db.commit()
        return exam_service.get_exam(db, principal.user, exam_id)

    return await run_in_threadpool(work)


@router.post("/{exam_id}/submit", response_model=ExamOut)
def submit_exam(
    exam_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    exam_service.submit_exam(db, principal.user, exam_id, principal.ip)
    db.commit()
    return exam_service.get_exam(db, principal.user, exam_id)


@router.post("/{exam_id}/generate")
def generate_paper(
    exam_id: str,
    background: BackgroundTasks,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    request = exam_service.queue_generation(db, principal.user, exam_id, principal.ip)
    db.commit()
    if settings.GEN_INLINE:
        background.add_task(generation_service.run_generation, request.id)
    return {
        "request_id": request.id,
        "exam_id": exam_id,
        "status": request.status,
        "mode": request.mode,
        "provider": request.provider,
        "inline": settings.GEN_INLINE,
    }


@router.get("/{exam_id}/generation", response_model=GenerationStatusOut)
def generation_status(
    exam_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    return exam_service.generation_status(db, principal.user, exam_id)


@router.get("/{exam_id}/paper", response_model=SecurePaperOut)
def secure_paper(
    exam_id: str,
    principal: Principal = Depends(require_staff),
    db: Session = Depends(get_db),
) -> dict:
    return exam_service.exam_secure_paper(db, principal.user, exam_id)


__all__ = ["router", "status"]
