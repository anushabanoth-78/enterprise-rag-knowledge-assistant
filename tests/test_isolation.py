"""The most important tests in the project: user A's data must be unreachable by user B.

Each case attacks a different layer: the HTTP routes, the retrieval SQL, and the conversation store.
"""
import uuid

from app.db import repository
from app.services import retrieval
from tests.helpers import fake_embed, register, upload_and_index

SECRET = "The merger codename is Project Falcon and the budget is forty million."


def two_users(client):
    ha, hb = register(client, "alice@example.com"), register(client, "bob@example.com")
    doc_id = upload_and_index(client, ha, [SECRET], "Confidential.pdf")
    return ha, hb, doc_id


# ---------------------------------------------------------------- HTTP layer
def test_other_users_document_routes_return_404(client):
    ha, hb, doc_id = two_users(client)
    for method, path in [("get", ""), ("get", "/status"), ("get", "/file"), ("post", "/reprocess"),
                         ("delete", "")]:
        r = getattr(client, method)(f"/api/documents/{doc_id}{path}", headers=hb)
        assert r.status_code == 404, f"{method.upper()} {path} leaked: {r.status_code}"
    # the 404 is indistinguishable from a document that never existed
    ghost = client.get(f"/api/documents/{uuid.uuid4()}", headers=hb)
    assert ghost.json() == client.get(f"/api/documents/{doc_id}", headers=hb).json()
    # and Alice's document is untouched by Bob's failed delete
    assert client.get(f"/api/documents/{doc_id}", headers=ha).status_code == 200


def test_listing_and_search_only_show_own_documents(client):
    ha, hb, _ = two_users(client)
    assert client.get("/api/documents", headers=hb).json()["total"] == 0
    assert client.get("/api/documents?q=Confidential", headers=hb).json()["total"] == 0
    assert client.get("/api/documents", headers=ha).json()["total"] == 1


def test_chat_never_cites_another_users_document(client, fakes):
    _, hb, doc_id = two_users(client)
    # Bob asks Alice's exact question, even naming her document id explicitly
    for body in ({"question": "What is the merger codename?"},
                 {"question": "What is the merger codename?", "document_ids": [doc_id]}):
        r = client.post("/api/chat/ask", headers=hb, json=body)
        assert r.status_code == 200
        out = r.json()
        assert out["citations"] == [] and out["answered"] is False
        assert "Falcon" not in str(out)
    assert fakes.llm_calls == []  # nothing relevant was found, so the LLM was never even called


def test_streaming_endpoint_is_isolated_too(client):
    _, hb, doc_id = two_users(client)
    r = client.post("/api/chat/ask/stream", headers=hb,
                    json={"question": "What is the merger codename?", "document_ids": [doc_id]})
    assert "Falcon" not in r.text and '"citations": []' in r.text


def test_compare_endpoint_is_isolated_too(client):
    _, hb, _ = two_users(client)
    r = client.post("/api/chat/compare", headers=hb, json={"question": "What is the merger codename?"})
    assert r.status_code == 200 and r.json()["citations"] == []


# ---------------------------------------------------------------- SQL layer (below the routes)
def test_retrieval_sql_is_scoped_to_owner(client, db):
    ha = register(client, "alice@example.com")
    bob_id = uuid.UUID(client.get("/api/auth/me", headers=register(client, "bob@example.com")).json()["id"])
    alice_doc = upload_and_index(client, ha, [SECRET], "Confidential.pdf")
    q = "merger codename Project Falcon budget"
    emb = fake_embed([q])[0]
    ids = [uuid.UUID(alice_doc)]
    assert repository.vector_search(db, bob_id, emb, 30) == []
    assert repository.vector_search(db, bob_id, emb, 30, ids) == []
    assert repository.keyword_search(db, bob_id, q, 30) == []
    assert repository.keyword_search(db, bob_id, q, 30, ids) == []
    assert retrieval.retrieve(db, bob_id, q, document_ids=ids) == []
    assert repository.get_document(db, bob_id, ids[0]) is None


def test_owner_still_retrieves_own_document(client, db):
    # guard against the isolation tests passing only because retrieval is broken
    ha = register(client, "alice@example.com")
    alice_id = uuid.UUID(client.get("/api/auth/me", headers=ha).json()["id"])
    upload_and_index(client, ha, [SECRET], "Confidential.pdf")
    found = retrieval.retrieve(db, alice_id, "merger codename Project Falcon")
    assert found and found[0].filename == "Confidential.pdf" and "Falcon" in found[0].content


def test_many_users_small_tenant_still_finds_its_chunk(client, db):
    """Regression for HNSW post-filtering: one user among many must still get results."""
    ha = register(client, "alice@example.com")
    alice_id = uuid.UUID(client.get("/api/auth/me", headers=ha).json()["id"])
    upload_and_index(client, ha, ["Alice private onboarding checklist for new engineers."], "Alice.pdf")
    for i in range(6):
        hx = register(client, f"noise{i}@example.com")
        upload_and_index(client, hx, [f"Noise document {i} about quarterly widgets and sprockets " * 8],
                         f"noise{i}.pdf")
    found = retrieval.retrieve(db, alice_id, "onboarding checklist new engineers")
    assert [p.filename for p in found] == ["Alice.pdf"]


# ---------------------------------------------------------------- conversations
def test_conversations_are_private(client):
    ha, hb, _ = two_users(client)
    conv = client.post("/api/chat/ask", headers=ha, json={"question": "What is the merger codename?"}).json()
    cid = conv["conversation_id"]
    assert client.get(f"/api/chat/{cid}", headers=ha).status_code == 200
    assert client.get(f"/api/chat/{cid}", headers=hb).status_code == 404
    assert client.get(f"/api/chat/{cid}/messages", headers=hb).status_code == 404
    assert client.delete(f"/api/chat/{cid}", headers=hb).status_code == 404
    # Bob cannot inject messages into Alice's conversation either
    r = client.post("/api/chat/ask", headers=hb, json={"question": "hello there", "conversation_id": cid})
    assert r.status_code == 404
    assert client.get("/api/chat/history", headers=hb).json()["total"] == 0
    assert client.get(f"/api/chat/{cid}", headers=ha).json()["messages"][0]["content"].startswith("What is")


def test_deleting_a_user_cascades_everything(client, db):
    from sqlalchemy import text
    ha, _, doc_id = two_users(client)
    client.post("/api/chat/ask", headers=ha, json={"question": "What is the merger codename?"})
    alice = repository.get_user_by_email(db, "alice@example.com")
    db.execute(text("DELETE FROM users WHERE id = :i"), {"i": alice.id})
    db.commit()
    for table in ("documents", "chunks", "conversations", "messages", "query_logs"):
        n = db.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        assert n == 0, f"{table} still has rows after user deletion"
