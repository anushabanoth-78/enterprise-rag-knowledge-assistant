"""Benchmark runner. Scores retrieval and generation separately.

Usage:
  python -m app.services.evaluation --email you@example.com --provider hosted
benchmark.jsonl line:
  {"id": "q1", "question": "...", "expected_source": "manual.pdf", "expected_keywords": ["torque", "12 Nm"]}
Add "expect_answer": false for a question your documents do NOT answer. The run then reports whether the
system correctly refused (the score that matters for hallucination control).
"""
import argparse
import csv
import datetime as dt
import json
import time
import uuid
from pathlib import Path

from app.core.config import settings
from app.db import repository
from app.services import generation, retrieval

FIELDS = ["run_id", "timestamp", "provider", "model", "rerank", "question_id", "hit_at_k",
          "keyword_recall", "has_citation", "citation_valid", "expect_answer", "refused", "retrieval_ms",
          "generation_ms", "prompt_tokens", "completion_tokens"]


def load_benchmark(path: Path | None = None) -> list[dict]:
    path = path or Path(settings.benchmark_path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def keyword_recall(answer: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    low = answer.lower()
    return sum(k.lower() in low for k in keywords) / len(keywords)


def run_benchmark(db, owner_id: uuid.UUID, provider: str, top_k: int = 5, rerank: bool = True) -> list[dict]:
    run_id, rows = uuid.uuid4().hex[:8], []
    for item in load_benchmark():
        t0 = time.perf_counter()
        passages = retrieval.retrieve(db, owner_id, item["question"], top_k=top_k, rerank=rerank)
        r_ms = (time.perf_counter() - t0) * 1000
        gen = generation.generate(provider, item["question"], passages)
        valid, invalid = generation.extract_citations(gen.answer, len(passages))
        expect = bool(item.get("expect_answer", True))
        rows.append({
            "run_id": run_id, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "provider": provider, "model": gen.model, "rerank": rerank, "question_id": item["id"],
            # retrieval quality: was the expected document in the evidence at all?
            "hit_at_k": int(any(p.filename == item.get("expected_source") for p in passages)),
            # generation quality
            "keyword_recall": round(keyword_recall(gen.answer, item.get("expected_keywords", [])), 3),
            "has_citation": int(bool(valid)), "citation_valid": int(bool(valid) and not invalid),
            "expect_answer": int(expect), "refused": int(not gen.answered),
            "retrieval_ms": round(r_ms, 1), "generation_ms": round(gen.latency_ms, 1),
            "prompt_tokens": gen.prompt_tokens, "completion_tokens": gen.completion_tokens,
        })
    results = Path(settings.results_path)
    results.parent.mkdir(parents=True, exist_ok=True)
    new = not results.exists() or results.stat().st_size == 0
    with results.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
    return rows


def read_results() -> list[dict]:
    results = Path(settings.results_path)
    if not results.exists():
        return []
    with results.open() as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--provider", choices=list(generation.PROVIDERS), default="hosted")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--no-rerank", action="store_true")
    a = ap.parse_args()
    with repository.SessionLocal() as session:
        user = repository.get_user_by_email(session, a.email)
        if not user:
            raise SystemExit("user not found")
        out = run_benchmark(session, user.id, a.provider, a.top_k, not a.no_rerank)
        n = len(out) or 1
        negatives = [r for r in out if not r["expect_answer"]]
        refusal = f"{sum(r['refused'] for r in negatives)/len(negatives):.2f}" if negatives else "n/a"
        print(f"{len(out)} questions | hit@k={sum(r['hit_at_k'] for r in out)/n:.2f} "
              f"kw_recall={sum(r['keyword_recall'] for r in out)/n:.2f} "
              f"valid_cites={sum(r['citation_valid'] for r in out)/n:.2f} correct_refusals={refusal}")
