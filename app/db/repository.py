"""All SQL lives here. Every function that touches user data takes owner_id and applies it in the query
itself, so a forgotten check in an API route cannot leak another user's rows."""
import datetime as dt
import uuid
from collections.abc import Iterator

import structlog
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, joinedload, sessionmaker

from app.core.config import settings
from app.db.models import Chunk, Conversation, Document, Message, QueryLog, User

log = structlog.get_logger()
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------- users
def create_user(db: Session, email: str, password_hash: str, is_admin: bool = False) -> User:
    user = User(email=email.lower(), password_hash=password_hash, is_admin=is_admin)
    db.add(user)
    db.commit()
    return user


def get_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


# ---------------------------------------------------------------- documents (always scoped by owner_id)
def create_document(db: Session, owner_id: uuid.UUID, filename: str, storage_key: str, sha256: str,
                    size_bytes: int) -> Document:
    doc = Document(owner_id=owner_id, filename=filename, storage_key=storage_key, sha256=sha256,
                   size_bytes=size_bytes)
    db.add(doc)
    db.commit()
    return doc


def get_document(db: Session, owner_id: uuid.UUID, doc_id: uuid.UUID) -> Document | None:
    return db.scalar(select(Document).where(Document.id == doc_id, Document.owner_id == owner_id))


def get_document_by_hash(db: Session, owner_id: uuid.UUID, sha256: str) -> Document | None:
    return db.scalar(select(Document).where(Document.owner_id == owner_id, Document.sha256 == sha256))


def count_documents(db: Session, owner_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(Document).where(Document.owner_id == owner_id)) or 0


def list_documents(db: Session, owner_id: uuid.UUID, q: str | None = None, limit: int = 50,
                   offset: int = 0) -> tuple[list[Document], int]:
    where = [Document.owner_id == owner_id]
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        where.append(Document.filename.ilike(f"%{escaped}%", escape="\\"))
    total = db.scalar(select(func.count()).select_from(Document).where(*where)) or 0
    rows = db.scalars(select(Document).where(*where).order_by(Document.created_at.desc())
                      .limit(limit).offset(offset))
    return list(rows), total


def delete_document(db: Session, doc: Document) -> None:
    db.delete(doc)  # chunks are removed by ON DELETE CASCADE
    db.commit()


# ---------------------------------------------------------------- retrieval primitives
_iterative_scan_supported = True


def _enable_iterative_scan(db: Session) -> None:
    """pgvector's HNSW index applies WHERE filters *after* the index scan, so with many users a small
    tenant's top-k can come back short or empty. Iterative scan (pgvector >= 0.8) keeps scanning until
    enough rows pass the filter. Older versions raise, so we try once inside a savepoint and remember."""
    global _iterative_scan_supported
    if not _iterative_scan_supported:
        return
    try:
        with db.begin_nested():
            db.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
            db.execute(text("SET LOCAL hnsw.max_scan_tuples = 20000"))
    except Exception:
        _iterative_scan_supported = False
        log.warning("hnsw_iterative_scan_unavailable", hint="upgrade pgvector to >= 0.8")


def vector_search(db: Session, owner_id: uuid.UUID, embedding: list[float], k: int,
                  document_ids: list[uuid.UUID] | None = None) -> list[tuple[Chunk, float]]:
    _enable_iterative_scan(db)
    dist = Chunk.embedding.cosine_distance(embedding).label("dist")
    stmt = select(Chunk, dist).options(joinedload(Chunk.document)).where(Chunk.owner_id == owner_id)
    if document_ids:
        stmt = stmt.where(Chunk.document_id.in_(document_ids))
    rows = db.execute(stmt.order_by(dist).limit(k)).unique().all()
    rows.sort(key=lambda r: r[1])  # relaxed_order may return slightly out-of-order rows
    return [(c, 1.0 - d) for c, d in rows]


def keyword_search(db: Session, owner_id: uuid.UUID, query: str, k: int,
                   document_ids: list[uuid.UUID] | None = None) -> list[tuple[Chunk, float]]:
    tsq = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(Chunk.tsv, tsq).label("rank")
    stmt = (select(Chunk, rank).options(joinedload(Chunk.document))
            .where(Chunk.owner_id == owner_id, Chunk.tsv.op("@@")(tsq)))
    if document_ids:
        stmt = stmt.where(Chunk.document_id.in_(document_ids))
    return [(c, float(r)) for c, r in db.execute(stmt.order_by(rank.desc()).limit(k)).unique().all()]


# ---------------------------------------------------------------- conversations
def create_conversation(db: Session, owner_id: uuid.UUID, title: str) -> Conversation:
    conv = Conversation(owner_id=owner_id, title=title[:200])
    db.add(conv)
    db.commit()
    return conv


def get_conversation(db: Session, owner_id: uuid.UUID, conv_id: uuid.UUID) -> Conversation | None:
    return db.scalar(select(Conversation).where(Conversation.id == conv_id, Conversation.owner_id == owner_id))


def list_conversations(db: Session, owner_id: uuid.UUID, limit: int = 50,
                       offset: int = 0) -> tuple[list[Conversation], int]:
    total = db.scalar(select(func.count()).select_from(Conversation)
                      .where(Conversation.owner_id == owner_id)) or 0
    rows = db.scalars(select(Conversation).where(Conversation.owner_id == owner_id)
                      .order_by(Conversation.updated_at.desc()).limit(limit).offset(offset))
    return list(rows), total


def delete_conversation(db: Session, conv: Conversation) -> None:
    db.delete(conv)
    db.commit()


def add_message(db: Session, conv: Conversation, role: str, content: str,
                citations: list | None = None, model: str | None = None) -> Message:
    msg = Message(conversation_id=conv.id, role=role, content=content, citations=citations, model=model)
    db.add(msg)
    conv.updated_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return msg


def list_messages(db: Session, conv: Conversation, limit: int = 200, offset: int = 0) -> list[Message]:
    return list(db.scalars(select(Message).where(Message.conversation_id == conv.id)
                           .order_by(Message.id).limit(limit).offset(offset)))


def recent_messages(db: Session, conv: Conversation, n: int) -> list[Message]:
    rows = list(db.scalars(select(Message).where(Message.conversation_id == conv.id)
                           .order_by(Message.id.desc()).limit(n)))
    return rows[::-1]


# ---------------------------------------------------------------- logs and stats
def add_query_log(db: Session, **kw) -> None:
    db.add(QueryLog(**kw))
    db.commit()


def usage_stats(db: Session, owner_id: uuid.UUID) -> list[dict]:
    stmt = (select(QueryLog.provider, QueryLog.model, func.count(), func.avg(QueryLog.retrieval_ms),
                   func.avg(QueryLog.generation_ms), func.sum(QueryLog.prompt_tokens),
                   func.sum(QueryLog.completion_tokens))
            .where(QueryLog.owner_id == owner_id).group_by(QueryLog.provider, QueryLog.model))
    return [dict(provider=p, model=m, queries=n, avg_retrieval_ms=round(r or 0, 1),
                 avg_generation_ms=round(g or 0, 1), prompt_tokens=int(pt or 0),
                 completion_tokens=int(ct or 0))
            for p, m, n, r, g, pt, ct in db.execute(stmt).all()]


def admin_metrics(db: Session) -> dict:
    """System-wide numbers for the admin dashboard. Aggregates only; no document or question content."""
    total_ms = QueryLog.retrieval_ms + QueryLog.generation_ms
    by_status = dict(db.execute(select(Document.status, func.count()).group_by(Document.status)).all())
    q = db.execute(select(
        func.count(), func.avg(total_ms), func.percentile_cont(0.95).within_group(total_ms),
        func.coalesce(func.sum(QueryLog.prompt_tokens), 0), func.coalesce(func.sum(QueryLog.completion_tokens), 0),
        func.count().filter(QueryLog.answered.is_(False)),
    )).one()
    return {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "documents": {"total": sum(by_status.values()), "by_status": by_status},
        "chunks": db.scalar(select(func.count()).select_from(Chunk)) or 0,
        "avg_processing_ms": round(db.scalar(select(func.avg(Document.processing_ms))) or 0, 1),
        "questions": {"total": q[0], "unanswered": q[5], "avg_ms": round(q[1] or 0, 1),
                      "p95_ms": round(q[2] or 0, 1), "prompt_tokens": int(q[3]), "completion_tokens": int(q[4])},
    }
