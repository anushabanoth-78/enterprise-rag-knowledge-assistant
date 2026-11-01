from app.core.config import settings
from tests.helpers import register, upload_and_index


def test_metrics_require_admin(client):
    h = register(client, "plain@example.com")
    assert client.get("/api/admin/metrics", headers=h).status_code == 403
    assert client.get("/api/evaluation/results", headers=h).status_code == 403
    assert client.get("/api/admin/metrics").status_code == 401


def test_admin_metrics_aggregate_the_whole_system(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com")
    boss = register(client, "boss@example.com")
    user = register(client, "user@example.com")
    upload_and_index(client, user, ["Leave policy is twenty days per year."], "HR.pdf")
    client.post("/api/documents", headers=user, files={"file": ("x.pdf", b"%PDF-1.7 garbage", "application/pdf")})
    client.post("/api/chat/ask", headers=user, json={"question": "How many leave days per year?"})
    client.post("/api/chat/ask", headers=user, json={"question": "quantum chromodynamics lagrangian spinor"})

    m = client.get("/api/admin/metrics", headers=boss).json()
    assert m["users"] == 2
    assert m["documents"]["by_status"] == {"ready": 1} and m["documents"]["total"] == 1
    assert m["chunks"] == 1 and m["avg_processing_ms"] > 0
    # both questions are logged; the refused one is flagged unanswered and cost zero tokens
    assert m["questions"]["total"] == 2 and m["questions"]["unanswered"] == 1
    assert m["questions"]["prompt_tokens"] == 10
    assert m["workers"]["workers_online"] == 1
    assert "http" in m
    assert client.get("/api/evaluation/results", headers=boss).status_code == 200
