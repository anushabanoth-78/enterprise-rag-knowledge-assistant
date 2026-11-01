import json

from app.core.config import settings
from app.services import generation
from tests.helpers import register, upload_and_index

LEAVE = "Employees are eligible for twenty annual leave days per calendar year."
WFH = "The work from home policy allows hybrid remote work three days per week."


def seed(client, h):
    upload_and_index(client, h, [WFH, "Filler page about parking and office plants.", LEAVE], "HR_Policy.pdf")
    upload_and_index(client, h, ["Expense reports are due on the fifth working day of each month."],
                     "Finance.pdf")


def test_answer_carries_page_level_citations(client):
    h = register(client, "a@example.com")
    seed(client, h)
    r = client.post("/api/chat/ask", headers=h, json={"question": "How many annual leave days per year?"})
    assert r.status_code == 200
    out = r.json()
    assert out["answered"] is True and "[1]" in out["answer"]
    top = out["citations"][0]
    assert top["filename"] == "HR_Policy.pdf" and top["page"] == 3  # the leave sentence is on page 3
    assert "twenty annual leave days" in top["text"]
    assert out["prompt_tokens"] == 10 and out["completion_tokens"] == 5


def test_multi_document_question_returns_sources_from_both_documents(client):
    h = register(client, "a@example.com")
    seed(client, h)
    r = client.post("/api/chat/ask", headers=h, json={
        "question": "annual leave days and when expense reports are due each month", "top_k": 5})
    files = {c["filename"] for c in r.json()["citations"]}
    assert files == {"HR_Policy.pdf", "Finance.pdf"}


def test_document_ids_filter_restricts_the_search(client):
    h = register(client, "a@example.com")
    seed(client, h)
    docs = {d["filename"]: d["id"] for d in client.get("/api/documents", headers=h).json()["items"]}
    r = client.post("/api/chat/ask", headers=h, json={
        "question": "annual leave days and expense reports due", "document_ids": [docs["Finance.pdf"]]})
    assert {c["filename"] for c in r.json()["citations"]} == {"Finance.pdf"}


def test_unanswerable_question_is_refused_without_calling_the_llm(client, fakes):
    h = register(client, "a@example.com")
    seed(client, h)
    r = client.post("/api/chat/ask", headers=h, json={"question": "quantum chromodynamics lagrangian spinor"})
    out = r.json()
    assert out["answered"] is False and out["answer"] == generation.NOT_FOUND_MESSAGE
    assert out["citations"] == [] and out["model"] == "none"
    assert fakes.llm_calls == []


def test_threshold_is_what_causes_the_refusal(client, monkeypatch):
    h = register(client, "a@example.com")
    seed(client, h)
    q = {"question": "quantum chromodynamics lagrangian spinor"}
    # Vector search always returns *something*, however irrelevant. With the safety net off, weak
    # passages reach the LLM; with it on, they are dropped and the system refuses.
    monkeypatch.setattr(settings, "min_rerank_score", -100.0)
    assert client.post("/api/chat/ask", headers=h, json=q).json()["citations"] != []
    monkeypatch.setattr(settings, "min_rerank_score", -3.0)
    assert client.post("/api/chat/ask", headers=h, json=q).json()["citations"] == []


def test_follow_up_uses_history_and_rewrites_the_query(client, fakes):
    h = register(client, "a@example.com")
    seed(client, h)
    first = client.post("/api/chat/ask", headers=h, json={"question": "How many annual leave days per year?"}).json()
    cid = first["conversation_id"]
    fakes.llm_calls.clear()
    second = client.post("/api/chat/ask", headers=h, json={"question": "and what about remote work?",
                                                           "conversation_id": cid})
    assert second.status_code == 200 and second.json()["conversation_id"] == cid
    rewrite_call, answer_call = fakes.llm_calls
    assert rewrite_call[0]["content"].startswith("Rewrite")
    roles = [m["role"] for m in answer_call]
    assert roles == ["system", "user", "assistant", "user"]  # prior turn is part of the prompt

    detail = client.get(f"/api/chat/{cid}", headers=h).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][1]["citations"][0]["page"] == 3  # citations survive a page reload
    assert client.get(f"/api/chat/{cid}/messages?limit=2", headers=h).json()[0]["role"] == "user"
    hist = client.get("/api/chat/history", headers=h).json()
    assert hist["total"] == 1 and hist["items"][0]["id"] == cid


def test_rewrite_failure_falls_back_to_the_raw_question(client, fakes, monkeypatch):
    h = register(client, "a@example.com")
    seed(client, h)
    cid = client.post("/api/chat/ask", headers=h, json={"question": "How many annual leave days?"}).json()[
        "conversation_id"]
    calls = {"n": 0}

    def flaky(messages):
        calls["n"] += 1
        if messages[0]["content"].startswith("Rewrite"):
            raise ConnectionError("llm down")
        return ("Still answers [1].", "fake-model", 1, 1)

    monkeypatch.setitem(generation.PROVIDERS, "hosted", flaky)
    r = client.post("/api/chat/ask", headers=h, json={"question": "annual leave days again", "conversation_id": cid})
    assert r.status_code == 200 and r.json()["answered"] is True


def test_missing_hosted_key_gives_503_and_leaves_no_empty_conversation(client, monkeypatch):
    h = register(client, "a@example.com")
    seed(client, h)
    # run the real provider code; HOSTED_API_KEY is empty in tests, so it must fail cleanly
    monkeypatch.setitem(generation.PROVIDERS, "hosted", generation._call_hosted)
    r = client.post("/api/chat/ask", headers=h, json={"question": "How many annual leave days per year?"})
    assert r.status_code == 503 and "HOSTED_API_KEY" in r.json()["detail"]
    assert client.get("/api/chat/history", headers=h).json()["total"] == 0


def test_validation(client):
    h = register(client, "a@example.com")
    assert client.post("/api/chat/ask", headers=h, json={"question": "hi"}).status_code == 422
    assert client.post("/api/chat/ask", headers=h, json={"question": "x" * 2001}).status_code == 422
    assert client.post("/api/chat/ask", headers=h, json={"question": "valid question", "top_k": 99}).status_code == 422
    assert client.post("/api/chat/ask", headers=h, json={
        "question": "valid question", "conversation_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404


def test_streaming_emits_meta_tokens_done_and_persists(client, monkeypatch):
    h = register(client, "a@example.com")
    seed(client, h)

    def fake_stream(provider, messages):
        yield {"type": "token", "text": "Twenty days "}
        yield {"type": "token", "text": "[1]."}
        yield {"type": "usage", "model": "fake-model", "prompt_tokens": 7, "completion_tokens": 3}

    monkeypatch.setattr(generation, "stream_provider", fake_stream)
    r = client.post("/api/chat/ask/stream", headers=h, json={"question": "How many annual leave days per year?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.split("\n\n") if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["meta", "token", "token", "done"]
    assert events[0]["citations"][0]["page"] == 3
    assert events[-1]["answered"] is True and events[-1]["prompt_tokens"] == 7
    detail = client.get(f"/api/chat/{events[0]['conversation_id']}", headers=h).json()
    assert detail["messages"][1]["content"] == "Twenty days [1]."


def test_streaming_provider_failure_emits_error_event_and_cleans_up(client, monkeypatch):
    import httpx
    h = register(client, "a@example.com")
    seed(client, h)

    def broken(provider, messages):
        raise httpx.ConnectError("down")
        yield  # pragma: no cover

    monkeypatch.setattr(generation, "stream_provider", broken)
    r = client.post("/api/chat/ask/stream", headers=h, json={"question": "How many annual leave days per year?"})
    assert '"type": "error"' in r.text
    assert client.get("/api/chat/history", headers=h).json()["total"] == 0


def test_compare_runs_both_providers_on_the_same_evidence(client, monkeypatch):
    h = register(client, "a@example.com")
    seed(client, h)

    def slow_local(messages):
        return ("Local says twenty [1].", "llama-test", 20, 8)

    monkeypatch.setitem(generation.PROVIDERS, "ollama", slow_local)
    r = client.post("/api/chat/compare", headers=h, json={"question": "How many annual leave days per year?"})
    assert r.status_code == 200
    out = r.json()
    assert {x["provider"] for x in out["results"]} == {"hosted", "ollama"}
    by = {x["provider"]: x for x in out["results"]}
    assert by["ollama"]["model"] == "llama-test" and by["ollama"]["prompt_tokens"] == 20
    assert by["hosted"]["valid_citations"] == [1]
    assert out["citations"][0]["page"] == 3
    same = client.post("/api/chat/compare", headers=h, json={"question": "valid question", "providers": ["hosted", "hosted"]})
    assert same.status_code == 422


def test_compare_reports_one_provider_failing_without_failing_the_request(client, monkeypatch):
    import httpx
    h = register(client, "a@example.com")
    seed(client, h)

    def down(messages):
        raise httpx.ConnectError("ollama not running")

    monkeypatch.setitem(generation.PROVIDERS, "ollama", down)
    out = client.post("/api/chat/compare", headers=h, json={"question": "How many annual leave days per year?"}).json()
    by = {x["provider"]: x for x in out["results"]}
    assert by["ollama"]["error"] == "ConnectError" and by["ollama"]["answer"] is None
    assert by["hosted"]["answer"]


def test_usage_stats_count_queries_per_model(client):
    h = register(client, "a@example.com")
    seed(client, h)
    client.post("/api/chat/ask", headers=h, json={"question": "How many annual leave days per year?"})
    usage = client.get("/api/evaluation/usage", headers=h).json()
    assert usage[0]["model"] == "fake-model" and usage[0]["queries"] == 1 and usage[0]["prompt_tokens"] == 10
