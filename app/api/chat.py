import json
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import rate_limit
from app.db import repository
from app.db.models import Conversation, User
from app.models.schemas import (ChatIn, ChatOut, CitationOut, CompareAnswer, CompareIn, CompareOut,
                                ConversationDetail, ConversationOut, MessageOut, Page)
from app.services import generation, retrieval
from app.services.retrieval import Passage

log = structlog.get_logger()
router = APIRouter(prefix="/chat", tags=["chat"])

LLM_ERRORS = (httpx.HTTPError, generation.LLMUnavailable)


def _llm_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, generation.LLMUnavailable):
        return HTTPException(503, str(exc))
    return HTTPException(502, f"LLM provider failed: {exc.__class__.__name__}")


def _citations(passages: list[Passage]) -> list[CitationOut]:
    return [CitationOut(ref=i, document_id=p.document_id, filename=p.filename, page=p.page,
                        text=p.content, score=round(p.score, 4)) for i, p in enumerate(passages, 1)]


@dataclass
class Prepared:
    conv_id: uuid.UUID
    is_new: bool
    history: list[dict]
    passages: list[Passage]
    retrieval_ms: float


def _prepare(db: Session, user: User, body: ChatIn) -> Prepared:
    """Resolve the conversation (ownership-checked), rewrite follow-ups, retrieve evidence."""
    if body.conversation_id:
        conv = repository.get_conversation(db, user.id, body.conversation_id)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        is_new = False
    else:
        conv = repository.create_conversation(db, user.id, body.question[:60])
        is_new = True
    history = [{"role": m.role, "content": m.content} for m in repository.recent_messages(db, conv, 6)]
    try:
        t0 = time.perf_counter()
        query = generation.rewrite_question(body.provider, body.question, history)
        passages = retrieval.retrieve(db, user.id, query, body.top_k, body.rerank, body.document_ids)
        retrieval_ms = (time.perf_counter() - t0) * 1000
    except Exception:
        if is_new:
            repository.delete_conversation(db, conv)
        raise
    return Prepared(conv.id, is_new, history, passages, retrieval_ms)


def _persist(db: Session, user_id: uuid.UUID, conv_id: uuid.UUID, question: str, answer: str,
             answered: bool, citations: list[CitationOut], provider: str, model: str, retrieval_ms: float,
             generation_ms: float, prompt_tokens: int, completion_tokens: int, n_valid: int) -> None:
    conv = repository.get_conversation(db, user_id, conv_id)
    if conv is None:
        return
    repository.add_message(db, conv, "user", question)
    repository.add_message(db, conv, "assistant", answer, [c.model_dump(mode="json") for c in citations], model)
    repository.add_query_log(
        db, owner_id=user_id, question=question, provider=provider, model=model, retrieval_ms=retrieval_ms,
        generation_ms=generation_ms, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        n_citations=n_valid, n_passages=len(citations), answered=answered)


# ------------------------------------------------------------------ conversations
@router.get("/history", response_model=Page[ConversationOut])
def history(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
            user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    items, total = repository.list_conversations(db, user.id, limit, offset)
    return Page(items=[ConversationOut.model_validate(c) for c in items], total=total, limit=limit,
                offset=offset)


def _owned_conversation(db: Session, user: User, conv_id: uuid.UUID) -> Conversation:
    conv = repository.get_conversation(db, user.id, conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


# ------------------------------------------------------------------ ask
@router.post("/ask", response_model=ChatOut)
def ask(body: ChatIn, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    prep = _prepare(db, user, body)
    try:
        gen = generation.generate(body.provider, body.question, prep.passages, prep.history)
    except LLM_ERRORS as exc:
        if prep.is_new:
            conv = repository.get_conversation(db, user.id, prep.conv_id)
            if conv:
                repository.delete_conversation(db, conv)
        raise _llm_http_error(exc)

    cites = _citations(prep.passages)
    valid, _ = generation.extract_citations(gen.answer, len(prep.passages))
    _persist(db, user.id, prep.conv_id, body.question, gen.answer, gen.answered, cites, body.provider,
             gen.model, prep.retrieval_ms, gen.latency_ms, gen.prompt_tokens, gen.completion_tokens, len(valid))
    return ChatOut(conversation_id=prep.conv_id, answer=gen.answer, answered=gen.answered,
                   provider=body.provider, model=gen.model, citations=cites,
                   retrieval_ms=round(prep.retrieval_ms, 1), generation_ms=round(gen.latency_ms, 1),
                   prompt_tokens=gen.prompt_tokens, completion_tokens=gen.completion_tokens)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/ask/stream")
def ask_stream(body: ChatIn, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    """Server-sent events: meta (citations) -> token* -> done | error. Retrieval happens before the
    stream starts, so ownership and validation errors are still ordinary HTTP errors."""
    prep = _prepare(db, user, body)
    cites = _citations(prep.passages)
    user_id, provider = user.id, body.provider

    def events() -> Iterator[str]:
        from app.db.repository import SessionLocal  # fresh session: the request session may be closed
        yield _sse({"type": "meta", "conversation_id": str(prep.conv_id),
                    "citations": [c.model_dump(mode="json") for c in cites],
                    "retrieval_ms": round(prep.retrieval_ms, 1)})
        if not prep.passages:
            nf = generation.not_found()
            yield _sse({"type": "token", "text": nf.answer})
            with SessionLocal() as s:
                _persist(s, user_id, prep.conv_id, body.question, nf.answer, False, cites, provider, nf.model,
                         prep.retrieval_ms, 0.0, 0, 0, 0)
            yield _sse({"type": "done", "answered": False, "model": nf.model, "generation_ms": 0,
                        "prompt_tokens": 0, "completion_tokens": 0})
            return
        parts: list[str] = []
        usage = {"model": "", "prompt_tokens": 0, "completion_tokens": 0}
        start = time.perf_counter()
        try:
            messages = generation.build_messages(body.question, prep.passages, prep.history)
            for ev in generation.stream_provider(provider, messages):
                if ev["type"] == "token":
                    parts.append(ev["text"])
                    yield _sse(ev)
                else:
                    usage = ev
        except (*LLM_ERRORS, json.JSONDecodeError) as exc:
            log.error("stream_failed", error=exc.__class__.__name__)
            with SessionLocal() as s:  # don't leave an empty conversation behind
                conv = repository.get_conversation(s, user_id, prep.conv_id)
                if conv and prep.is_new:
                    repository.delete_conversation(s, conv)
            yield _sse({"type": "error", "message": f"LLM provider failed: {exc.__class__.__name__}"})
            return
        answer = "".join(parts)
        ms = (time.perf_counter() - start) * 1000
        valid, _ = generation.extract_citations(answer, len(prep.passages))
        with SessionLocal() as s:
            _persist(s, user_id, prep.conv_id, body.question, answer, bool(valid), cites, provider,
                     usage["model"], prep.retrieval_ms, ms, usage["prompt_tokens"],
                     usage["completion_tokens"], len(valid))
        yield _sse({"type": "done", "answered": bool(valid), "model": usage["model"],
                    "generation_ms": round(ms, 1), "prompt_tokens": usage["prompt_tokens"],
                    "completion_tokens": usage["completion_tokens"]})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ compare two LLMs on the same evidence
@router.post("/compare", response_model=CompareOut)
def compare(body: CompareIn, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    if body.providers[0] == body.providers[1]:
        raise HTTPException(422, "Choose two different providers")
    t0 = time.perf_counter()
    passages = retrieval.retrieve(db, user.id, body.question, body.top_k, body.rerank, body.document_ids)
    retrieval_ms = (time.perf_counter() - t0) * 1000
    cites = _citations(passages)

    def run(provider: str) -> CompareAnswer:
        try:
            gen = generation.generate(provider, body.question, passages)
            valid, _ = generation.extract_citations(gen.answer, len(passages))
            return CompareAnswer(provider=provider, model=gen.model, answer=gen.answer,
                                 generation_ms=round(gen.latency_ms, 1), prompt_tokens=gen.prompt_tokens,
                                 completion_tokens=gen.completion_tokens, valid_citations=valid)
        except LLM_ERRORS as exc:
            msg = str(exc) if isinstance(exc, generation.LLMUnavailable) else exc.__class__.__name__
            return CompareAnswer(provider=provider, model="", error=msg)

    with ThreadPoolExecutor(max_workers=2) as pool:  # same evidence, both models at once
        results = list(pool.map(run, body.providers))
    for r in results:  # DB writes stay on the request thread: sessions are not thread-safe
        if r.answer is not None:
            repository.add_query_log(
                db, owner_id=user.id, question=body.question, provider=r.provider, model=r.model,
                retrieval_ms=retrieval_ms, generation_ms=r.generation_ms, prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens, n_citations=len(r.valid_citations),
                n_passages=len(passages), answered=bool(r.valid_citations))
    return CompareOut(question=body.question, retrieval_ms=round(retrieval_ms, 1), citations=cites,
                      results=results)


# ------------------------------------------------------------------ conversation detail (keep after static routes)
@router.get("/{conv_id}", response_model=ConversationDetail)
def conversation(conv_id: uuid.UUID, user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    return ConversationDetail.model_validate(_owned_conversation(db, user, conv_id))


@router.get("/{conv_id}/messages", response_model=list[MessageOut])
def messages(conv_id: uuid.UUID, limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
             user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    conv = _owned_conversation(db, user, conv_id)
    return [MessageOut.model_validate(m) for m in repository.list_messages(db, conv, limit, offset)]


@router.delete("/{conv_id}", status_code=204)
def delete_conversation(conv_id: uuid.UUID, user: User = Depends(rate_limit),
                        db: Session = Depends(repository.get_db)):
    repository.delete_conversation(db, _owned_conversation(db, user, conv_id))
