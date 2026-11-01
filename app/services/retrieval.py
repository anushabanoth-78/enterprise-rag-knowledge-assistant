"""Hybrid retrieval: pgvector + Postgres full-text, fused with RRF, then cross-encoder rerank.

The final step drops passages the reranker scores below `min_rerank_score`. That threshold is what lets
the system say "not in your documents" instead of handing the LLM weak evidence and hoping it behaves."""
import functools
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import repository
from app.services.ingestion import embed_texts

RRF_K = 60


@dataclass
class Passage:
    chunk_id: int
    document_id: uuid.UUID
    filename: str
    page: int
    content: str
    score: float
    reranked: bool = False


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Each ranking is a list of ids, best first. Returns id -> fused score."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores


def filter_by_score(passages: list[Passage], min_score: float) -> list[Passage]:
    """Keep reranked passages at or above the threshold. Un-reranked scores (RRF) are not comparable to
    cross-encoder logits, so they are never filtered here."""
    return [p for p in passages if not p.reranked or p.score >= min_score]


@functools.lru_cache(maxsize=1)
def _reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(settings.reranker_model)


def retrieve(db: Session, owner_id: uuid.UUID, query: str, top_k: int = 8, rerank: bool = True,
             document_ids: list[uuid.UUID] | None = None, candidates: int | None = None,
             mode: str = "hybrid", apply_threshold: bool = True) -> list[Passage]:
    candidates = candidates or settings.candidates
    vec_hits: list = []
    kw_hits: list = []
    if mode in ("hybrid", "vector"):
        vec_hits = repository.vector_search(db, owner_id, embed_texts([query])[0], candidates, document_ids)
    if mode in ("hybrid", "keyword"):
        kw_hits = repository.keyword_search(db, owner_id, query, candidates, document_ids)

    by_id = {c.id: c for c, _ in vec_hits + kw_hits}
    fused = reciprocal_rank_fusion([[c.id for c, _ in vec_hits], [c.id for c, _ in kw_hits]])
    ordered = sorted(fused, key=fused.get, reverse=True)[: max(candidates, top_k)]
    if not ordered:
        return []

    if rerank:
        scores = _reranker().predict([(query, by_id[i].content) for i in ordered])
        scored = sorted(zip(ordered, (float(s) for s in scores)), key=lambda x: x[1], reverse=True)
    else:
        scored = [(i, fused[i]) for i in ordered]

    passages = [
        Passage(i, by_id[i].document_id, by_id[i].document.filename, by_id[i].page, by_id[i].content, s,
                reranked=rerank)
        for i, s in scored
    ]
    if rerank and apply_threshold:
        passages = filter_by_score(passages, settings.min_rerank_score)
    # When several documents are selected, keep the best passage(s) of each so one file cannot crowd out the rest.
    if document_ids and len(document_ids) > 1:
        picked, seen = [], {}
        for p in passages:                      # passages are already sorted best-first
            if seen.get(p.document_id, 0) < 2:  # up to 2 passages per document first
                picked.append(p)
                seen[p.document_id] = seen.get(p.document_id, 0) + 1
        picked_ids = {x.chunk_id for x in picked}
        rest = [x for x in passages if x.chunk_id not in picked_ids]
        passages = sorted(picked, key=lambda x: x.score, reverse=True) + rest
    return passages[:top_k]
