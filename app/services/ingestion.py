"""Extract text -> chunk -> embed -> index. Runs inside the Celery worker."""
import functools
import time
import uuid

import fitz  # PyMuPDF
import structlog

from app.core.config import settings

log = structlog.get_logger()


class PermanentIngestError(Exception):
    """The document itself is the problem (scanned, too long, corrupt). Retrying cannot help."""


def open_pdf(data: bytes) -> "fitz.Document":
    try:
        return fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # fitz raises several exception types for corrupt files
        raise PermanentIngestError(f"Could not open PDF: {exc}") from exc


def validate_pdf(data: bytes, max_pages: int) -> int:
    """Used at upload time to reject bad files early. Returns the page count."""
    with open_pdf(data) as pdf:
        if pdf.needs_pass:
            raise PermanentIngestError("PDF is password protected")
        if pdf.page_count == 0:
            raise PermanentIngestError("PDF has no pages")
        if pdf.page_count > max_pages:
            raise PermanentIngestError(f"PDF has {pdf.page_count} pages; the limit is {max_pages}")
        return pdf.page_count


def extract_pages(data: bytes) -> list[tuple[int, str]]:
    with open_pdf(data) as pdf:
        return [(i + 1, page.get_text()) for i, page in enumerate(pdf)]


def chunk_pages(pages: list[tuple[int, str]], size: int, overlap: int) -> list[tuple[int, str]]:
    """Sliding word window per page so every chunk keeps an exact page number for citations."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than chunk size")
    out: list[tuple[int, str]] = []
    step = size - overlap
    for page_no, text in pages:
        words = text.split()
        for start in range(0, len(words), step):
            out.append((page_no, " ".join(words[start:start + size])))
            if start + size >= len(words):
                break
    return out


@functools.lru_cache(maxsize=1)
def _embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    vecs = _embedder().encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
    return vecs.tolist()


def process_document(doc_id: uuid.UUID, force: bool = False) -> None:
    """Idempotent: a redelivered task for an already-indexed document is a no-op unless force=True."""
    from app.db.models import Chunk, Document
    from app.db.repository import SessionLocal
    from app.storage import get_storage

    started = time.perf_counter()
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            return
        if doc.status == "ready" and not force:
            log.info("ingestion_skipped_already_ready", document_id=str(doc.id))
            return
        doc.status, doc.error = "processing", None
        db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
        db.commit()
        try:
            data = get_storage().get(doc.storage_key)
            pages = extract_pages(data)
            if len(pages) > settings.max_pages_per_document:
                raise PermanentIngestError(f"PDF has {len(pages)} pages; the limit is "
                                           f"{settings.max_pages_per_document}")
            chunks = chunk_pages(pages, settings.chunk_words, settings.chunk_overlap)
            if not chunks:
                raise PermanentIngestError("No extractable text (scanned PDF? OCR is not enabled)")
            vectors = embed_texts([c for _, c in chunks])
            db.add_all([
                Chunk(document_id=doc.id, owner_id=doc.owner_id, page=p, idx=i, content=t, embedding=v)
                for i, ((p, t), v) in enumerate(zip(chunks, vectors))
            ])
            doc.n_pages, doc.n_chunks, doc.status = len(pages), len(chunks), "ready"
            doc.processing_ms = round((time.perf_counter() - started) * 1000, 1)
            db.commit()
            log.info("ingestion_done", document_id=str(doc.id), chunks=len(chunks), ms=doc.processing_ms)
        except PermanentIngestError as exc:
            _mark_failed(db, doc_id, str(exc))
            log.warning("ingestion_failed_permanent", document_id=str(doc_id), error=str(exc))
            # not re-raised: Celery must not retry a document that can never succeed
        except Exception as exc:
            _mark_failed(db, doc_id, f"{exc.__class__.__name__}: {exc}")
            log.error("ingestion_failed", document_id=str(doc_id), error=str(exc))
            raise  # transient (DB, storage, model download): let Celery retry with backoff


def _mark_failed(db, doc_id: uuid.UUID, message: str) -> None:
    from app.db.models import Document
    db.rollback()
    doc = db.get(Document, doc_id)
    if doc is not None:
        doc.status, doc.error = "failed", message[:500]
        db.commit()
