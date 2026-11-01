import hashlib
import os
import uuid
from urllib.parse import quote

import structlog
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import rate_limit
from app.db import repository
from app.db.models import Document, User
from app.models.schemas import DocumentOut, JobOut, Page
from app.services.ingestion import PermanentIngestError, validate_pdf
from app.storage import get_storage
from app.worker import celery_app, ingest_document_task

log = structlog.get_logger()
router = APIRouter(prefix="/documents", tags=["documents"])


def _get_owned(db: Session, user: User, doc_id: uuid.UUID) -> Document:
    # 404 (not 403) for other people's documents so IDs cannot be probed for existence.
    doc = repository.get_document(db, user.id, doc_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """Read the upload in chunks and abort as soon as it exceeds the limit."""
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                f"File too large (limit {settings.max_upload_mb} MB)")
    return bytes(buf)


def _dispatch(db: Session, doc: Document, force: bool = False) -> None:
    """Queue ingestion. If the broker is down the document is marked failed (reprocessable), never lost."""
    try:
        doc.task_id = ingest_document_task.delay(str(doc.id), force).id
        db.commit()
    except Exception as exc:  # noqa: BLE001 - kombu raises several connection error types
        log.error("task_dispatch_failed", document_id=str(doc.id), error=exc.__class__.__name__)
        doc.status, doc.error = "failed", "Processing queue unavailable. Use Reprocess to retry."
        db.commit()


@router.post("", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload(file: UploadFile = File(...), user: User = Depends(rate_limit),
                 db: Session = Depends(repository.get_db)):
    filename = os.path.basename(file.filename or "")[:255]
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF files are supported")
    if repository.count_documents(db, user.id) >= settings.max_documents_per_user:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Document limit reached ({settings.max_documents_per_user}). Delete some first.")

    data = await _read_limited(file, settings.max_upload_mb * 1024 * 1024)
    if not data.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is not a valid PDF")
    try:
        await run_in_threadpool(validate_pdf, data, settings.max_pages_per_document)
    except PermanentIngestError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    sha = hashlib.sha256(data).hexdigest()
    if repository.get_document_by_hash(db, user.id, sha):
        raise HTTPException(status.HTTP_409_CONFLICT, "You already uploaded this exact file")

    key = f"{user.id}/{uuid.uuid4().hex}.pdf"
    await run_in_threadpool(get_storage().put, key, data)
    try:
        doc = repository.create_document(db, user.id, filename, key, sha, len(data))
    except IntegrityError:  # same file uploaded twice at the same moment
        db.rollback()
        await run_in_threadpool(get_storage().delete, key)
        raise HTTPException(status.HTTP_409_CONFLICT, "You already uploaded this exact file")
    _dispatch(db, doc)
    return doc


@router.get("", response_model=Page[DocumentOut])
def list_docs(q: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=200),
              offset: int = Query(0, ge=0), user: User = Depends(rate_limit),
              db: Session = Depends(repository.get_db)):
    items, total = repository.list_documents(db, user.id, q, limit, offset)
    return Page(items=[DocumentOut.model_validate(d) for d in items], total=total, limit=limit, offset=offset)


@router.get("/{doc_id}", response_model=DocumentOut)
def get_doc(doc_id: uuid.UUID, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    return _get_owned(db, user, doc_id)


@router.get("/{doc_id}/status", response_model=JobOut)
def job_status(doc_id: uuid.UUID, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    doc = _get_owned(db, user, doc_id)
    state = None
    if doc.task_id:
        try:
            state = AsyncResult(doc.task_id, app=celery_app).state
        except Exception as exc:  # noqa: BLE001 - result backend (Redis) unreachable
            # The database status is authoritative; Celery's view is a bonus and must not take this down.
            log.warning("celery_state_unavailable", error=exc.__class__.__name__)
    return JobOut(document_id=doc.id, status=doc.status, celery_state=state, error=doc.error)


@router.get("/{doc_id}/file")
def download(doc_id: uuid.UUID, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    """Serves the original PDF (owner only). The frontend opens it at the cited page."""
    doc = _get_owned(db, user, doc_id)
    try:
        data = get_storage().get(doc.storage_key)
    except Exception:  # noqa: BLE001 - FileNotFoundError locally, ClientError on S3
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file is missing")
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.filename)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"})


@router.post("/{doc_id}/reprocess", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
def reprocess(doc_id: uuid.UUID, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    doc = _get_owned(db, user, doc_id)
    if doc.status in ("queued", "processing"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Document is already being processed")
    doc.status, doc.error = "queued", None
    db.commit()
    _dispatch(db, doc, force=True)
    return doc


@router.delete("/{doc_id}", status_code=204)
def delete(doc_id: uuid.UUID, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    doc = _get_owned(db, user, doc_id)
    key = doc.storage_key
    repository.delete_document(db, doc)  # chunks + embeddings go with it (ON DELETE CASCADE)
    try:
        get_storage().delete(key)
    except Exception:  # noqa: BLE001 - the DB row is already gone; an orphaned object is only a leak
        log.error("storage_delete_failed", key=key)
