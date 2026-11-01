import os
import uuid

import pytest

from app.core.config import settings
from app.db.models import Chunk
from app.services.ingestion import process_document
from tests.helpers import make_pdf, register, upload, upload_and_index


def test_upload_returns_202_and_queues_exactly_one_task(client, fakes):
    h = register(client, "a@example.com")
    r = upload(client, h, ["Annual leave is twenty days."], "HR_Policy.pdf")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued" and body["filename"] == "HR_Policy.pdf"
    assert fakes.queued == [(body["id"], False)]
    st = client.get(f"/api/documents/{body['id']}/status", headers=h)
    assert st.status_code == 200 and st.json()["status"] == "queued"


def test_status_endpoint_survives_an_unreachable_result_backend(client, monkeypatch):
    from app.api import documents
    h = register(client, "a@example.com")
    doc_id = upload(client, h, ["status check"]).json()["id"]

    class Broken:
        def __init__(self, *a, **k):
            pass

        @property
        def state(self):
            raise ConnectionError("redis unreachable")

    monkeypatch.setattr(documents, "AsyncResult", Broken)
    r = client.get(f"/api/documents/{doc_id}/status", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "queued" and r.json()["celery_state"] is None


def test_indexing_pipeline_stores_chunks_with_correct_pages(client, db):
    h = register(client, "a@example.com")
    doc_id = upload_and_index(client, h, ["Leave policy twenty days.", "Remote work policy hybrid model."])
    doc = client.get(f"/api/documents/{doc_id}", headers=h).json()
    assert doc["status"] == "ready" and doc["n_pages"] == 2 and doc["n_chunks"] == 2
    assert doc["processing_ms"] is not None
    pages = {c.page: c.content for c in db.query(Chunk).filter(Chunk.document_id == uuid.UUID(doc_id))}
    assert "Leave" in pages[1] and "Remote" in pages[2]


@pytest.mark.parametrize("name,data,code", [
    ("notes.txt", b"hello", 415),
    ("fake.pdf", b"this is not a pdf", 400),
    ("broken.pdf", b"%PDF-1.7 garbage", 422),
])
def test_upload_validation(client, name, data, code):
    h = register(client, "a@example.com")
    r = client.post("/api/documents", headers=h, files={"file": (name, data, "application/pdf")})
    assert r.status_code == code


def test_oversized_upload_rejected(client, monkeypatch):
    h = register(client, "a@example.com")
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    assert upload(client, h, ["x"]).status_code == 413


def test_page_limit_enforced_at_upload(client, monkeypatch):
    h = register(client, "a@example.com")
    monkeypatch.setattr(settings, "max_pages_per_document", 2)
    r = upload(client, h, ["a", "b", "c"])
    assert r.status_code == 422 and "limit" in r.json()["detail"]


def test_duplicate_file_is_rejected_not_reingested(client, fakes):
    h = register(client, "a@example.com")
    data = make_pdf(["same content"])
    files = {"file": ("a.pdf", data, "application/pdf")}
    assert client.post("/api/documents", headers=h, files=files).status_code == 202
    assert client.post("/api/documents", headers=h, files=files).status_code == 409
    assert len(fakes.queued) == 1


def test_same_file_by_two_users_is_allowed(client):
    data = make_pdf(["shared handbook"])
    files = {"file": ("a.pdf", data, "application/pdf")}
    ha, hb = register(client, "a@example.com"), register(client, "b@example.com")
    assert client.post("/api/documents", headers=ha, files=files).status_code == 202
    assert client.post("/api/documents", headers=hb, files=files).status_code == 202


def test_document_count_limit(client, monkeypatch):
    h = register(client, "a@example.com")
    monkeypatch.setattr(settings, "max_documents_per_user", 1)
    assert upload(client, h, ["one"]).status_code == 202
    assert upload(client, h, ["two"]).status_code == 409


def test_broker_down_marks_document_failed_and_reprocess_recovers(client, fakes, monkeypatch):
    from app.api import documents
    h = register(client, "a@example.com")
    working_delay = documents.ingest_document_task.delay

    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(documents.ingest_document_task, "delay", boom)
    r = upload(client, h, ["recover me"])
    # the file and row are kept (not lost); the user can retry
    assert r.status_code == 202 and r.json()["status"] == "failed"
    assert "Reprocess" in r.json()["error"]

    monkeypatch.setattr(documents.ingest_document_task, "delay", working_delay)  # broker is back
    again = client.post(f"/api/documents/{r.json()['id']}/reprocess", headers=h)
    assert again.status_code == 202 and again.json()["status"] == "queued"
    assert fakes.queued[-1] == (r.json()["id"], True)


def test_reprocess_requeues_with_force(client, fakes):
    h = register(client, "a@example.com")
    doc_id = upload_and_index(client, h, ["some searchable text"])
    r = client.post(f"/api/documents/{doc_id}/reprocess", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert fakes.queued[-1] == (doc_id, True)
    # while it is queued, a second reprocess is refused
    assert client.post(f"/api/documents/{doc_id}/reprocess", headers=h).status_code == 409


def test_scanned_pdf_fails_permanently_without_retry(client, db):
    import fitz
    h = register(client, "a@example.com")
    blank = fitz.open()
    blank.new_page()
    r = client.post("/api/documents", headers=h, files={"file": ("scan.pdf", blank.tobytes(), "application/pdf")})
    doc_id = r.json()["id"]
    process_document(uuid.UUID(doc_id))  # must NOT raise (a raise would make Celery retry forever-failing work)
    doc = client.get(f"/api/documents/{doc_id}", headers=h).json()
    assert doc["status"] == "failed" and "No extractable text" in doc["error"]


def test_redelivered_task_for_ready_document_is_a_noop(client, db):
    h = register(client, "a@example.com")
    doc_id = upload_and_index(client, h, ["content that is indexed once"])
    before = db.query(Chunk).filter(Chunk.document_id == uuid.UUID(doc_id)).count()
    process_document(uuid.UUID(doc_id))  # at-least-once delivery (acks_late) means this can happen
    db.expire_all()
    assert db.query(Chunk).filter(Chunk.document_id == uuid.UUID(doc_id)).count() == before


def test_forced_reprocess_replaces_chunks_without_duplicates(client, db):
    h = register(client, "a@example.com")
    doc_id = upload_and_index(client, h, ["alpha", "beta"])
    process_document(uuid.UUID(doc_id), force=True)
    db.expire_all()
    assert db.query(Chunk).filter(Chunk.document_id == uuid.UUID(doc_id)).count() == 2


def test_list_pagination_and_filename_search(client):
    h = register(client, "a@example.com")
    for name in ["HR_Policy.pdf", "Handbook.pdf", "Finance_Report.pdf"]:
        assert upload(client, h, [name], name).status_code == 202
    page = client.get("/api/documents?limit=2&offset=0", headers=h).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["limit"] == 2
    found = client.get("/api/documents?q=policy", headers=h).json()
    assert [d["filename"] for d in found["items"]] == ["HR_Policy.pdf"]
    assert client.get("/api/documents?q=%25", headers=h).json()["total"] == 0  # '%' is literal, not a wildcard
    assert client.get("/api/documents?limit=0", headers=h).status_code == 422


def test_file_endpoint_returns_the_original_pdf(client):
    h = register(client, "a@example.com")
    original = make_pdf(["download me"])
    r = client.post("/api/documents", headers=h, files={"file": ("a.pdf", original, "application/pdf")})
    got = client.get(f"/api/documents/{r.json()['id']}/file", headers=h)
    assert got.status_code == 200 and got.content == original
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["x-content-type-options"] == "nosniff"


def test_delete_removes_row_chunks_and_stored_file(client, db, fakes):
    h = register(client, "a@example.com")
    doc_id = upload_and_index(client, h, ["to be deleted"])
    assert db.query(Chunk).filter(Chunk.document_id == uuid.UUID(doc_id)).count() == 1
    assert os.listdir(fakes.storage.root)
    assert client.delete(f"/api/documents/{doc_id}", headers=h).status_code == 204
    db.expire_all()
    assert db.query(Chunk).filter(Chunk.document_id == uuid.UUID(doc_id)).count() == 0
    assert client.get(f"/api/documents/{doc_id}", headers=h).status_code == 404
    user_dirs = [d for d in os.listdir(fakes.storage.root)]
    assert all(not os.listdir(os.path.join(fakes.storage.root, d)) for d in user_dirs)
