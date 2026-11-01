import csv

from app.core.config import settings
from app.services import evaluation
from app.services.evaluation import keyword_recall


def test_keyword_recall():
    assert keyword_recall("The torque is 12 Nm", ["torque", "12 nm", "bolt"]) == 2 / 3
    assert keyword_recall("anything", []) == 1.0


def test_results_roundtrip_uses_configured_path(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "results_path", str(tmp_path / "nested" / "results.csv"))
    assert evaluation.read_results() == []
    row = {f: 0 for f in evaluation.FIELDS}
    path = tmp_path / "nested"
    path.mkdir()
    with open(settings.results_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=evaluation.FIELDS)
        w.writeheader()
        w.writerow(row)
    assert len(evaluation.read_results()) == 1


def test_benchmark_loader_skips_blank_lines(tmp_path, monkeypatch):
    p = tmp_path / "b.jsonl"
    p.write_text('{"id": "q1", "question": "x"}\n\n{"id": "q2", "question": "y", "expect_answer": false}\n')
    monkeypatch.setattr(settings, "benchmark_path", str(p))
    items = evaluation.load_benchmark()
    assert [i["id"] for i in items] == ["q1", "q2"] and items[1]["expect_answer"] is False


def test_benchmark_run_scores_retrieval_citations_and_refusals(client, db, tmp_path, monkeypatch):
    """End to end: real SQL retrieval over an indexed PDF, the LLM faked, results written to CSV."""
    import json
    import uuid

    from tests.helpers import register, upload_and_index

    h = register(client, "eval@example.com")
    user_id = uuid.UUID(client.get("/api/auth/me", headers=h).json()["id"])
    upload_and_index(client, h, ["Cover page.", "Employees get twenty annual leave days per calendar year."],
                     "HR_Policy.pdf")

    bench = tmp_path / "bench.jsonl"
    bench.write_text("\n".join(json.dumps(x) for x in [
        {"id": "q1", "question": "How many annual leave days per calendar year?",
         "expected_source": "HR_Policy.pdf", "expected_keywords": ["answer", "sources"]},
        {"id": "q2", "question": "quantum chromodynamics lagrangian spinor", "expected_source": "",
         "expected_keywords": [], "expect_answer": False},
    ]))
    monkeypatch.setattr(settings, "benchmark_path", str(bench))
    monkeypatch.setattr(settings, "results_path", str(tmp_path / "out" / "results.csv"))

    rows = evaluation.run_benchmark(db, user_id, "hosted", 5, True)
    by = {r["question_id"]: r for r in rows}
    assert by["q1"]["hit_at_k"] == 1 and by["q1"]["citation_valid"] == 1 and by["q1"]["refused"] == 0
    assert by["q2"]["expect_answer"] == 0 and by["q2"]["refused"] == 1  # correctly refused
    saved = evaluation.read_results()
    assert [r["question_id"] for r in saved] == ["q1", "q2"]
    assert saved[1]["expect_answer"] == "0" and saved[1]["refused"] == "1"
