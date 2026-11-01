"""Test harness.

Unit tests need nothing. Integration tests (anything using the `client` fixture) need a Postgres with the
pgvector extension: set TEST_DATABASE_URL, e.g.
    TEST_DATABASE_URL=postgresql+psycopg://rag:rag@localhost:5432/rag_test pytest
They are skipped when it is not set. Only the ML models, the LLM, Redis and Celery are faked; SQL runs for
real, so the user-isolation tests exercise the actual queries.
"""
import os
import types

import pytest

# Must happen before any `app` import: settings are read once at import time.
_TEST_DB = os.environ.get("TEST_DATABASE_URL")
if _TEST_DB:
    os.environ["DATABASE_URL"] = _TEST_DB
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ["HOSTED_API_KEY"] = ""  # tests must never reach a real LLM

from fastapi.testclient import TestClient  # noqa: E402

from tests.helpers import FakeRedis, FakeReranker, fake_embed  # noqa: E402


@pytest.fixture(scope="session")
def _schema():
    if not _TEST_DB:
        pytest.skip("set TEST_DATABASE_URL to run integration tests")
    from alembic import command
    from alembic.config import Config
    root = os.path.join(os.path.dirname(__file__), "..")
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    command.upgrade(cfg, "head")  # also proves the migrations apply to an empty database


@pytest.fixture(autouse=True)
def fakes(monkeypatch, tmp_path):
    """Replace everything slow or external. Returns spies: .queued (Celery dispatches), .llm_calls."""
    from app import storage
    from app.api import admin, documents
    from app.core import metrics, security
    from app.services import generation, ingestion, retrieval

    fake_redis = FakeRedis()
    monkeypatch.setattr(security, "_redis", fake_redis)
    monkeypatch.setattr(metrics, "_redis", fake_redis)
    monkeypatch.setattr(metrics, "record_request", lambda *a, **k: None)
    monkeypatch.setattr(admin, "worker_status", lambda: {"workers_online": 1, "active_tasks": 0,
                                                         "queued_tasks": 0, "reachable": True})
    monkeypatch.setattr(ingestion, "embed_texts", fake_embed)
    monkeypatch.setattr(retrieval, "embed_texts", fake_embed)
    monkeypatch.setattr(retrieval, "_reranker", lambda: FakeReranker())

    local = storage.LocalStorage(str(tmp_path / "files"))
    monkeypatch.setattr(storage, "get_storage", lambda: local)
    monkeypatch.setattr(documents, "get_storage", lambda: local)

    spies = types.SimpleNamespace(queued=[], llm_calls=[], redis=fake_redis, storage=local)

    def fake_delay(doc_id, force=False):
        spies.queued.append((doc_id, force))
        return types.SimpleNamespace(id=f"task-{len(spies.queued)}")

    monkeypatch.setattr(documents.ingest_document_task, "delay", fake_delay)

    def fake_llm(messages):
        spies.llm_calls.append(messages)
        if messages[0]["content"].startswith("Rewrite the user's latest question"):
            return (messages[-1]["content"].split("Latest question:", 1)[1].strip(), "fake-model", 1, 1)
        return ("The answer is in the sources [1].", "fake-model", 10, 5)

    monkeypatch.setitem(generation.PROVIDERS, "hosted", fake_llm)
    monkeypatch.setitem(generation.PROVIDERS, "ollama", fake_llm)
    return spies


@pytest.fixture
def client(_schema):
    from sqlalchemy import text

    from app.db.repository import engine
    from app.main import app
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE users, documents, chunks, conversations, messages, query_logs "
                          "RESTART IDENTITY CASCADE"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(_schema):
    from app.db.repository import SessionLocal
    with SessionLocal() as session:
        yield session
