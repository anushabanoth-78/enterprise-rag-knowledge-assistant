"""Shared test helpers: deterministic fakes and small API helpers. No `app` imports at module level."""
import math
import re
import zlib

import fitz
from fastapi.testclient import TestClient

DIM = 384


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Bag-of-words hashing embedding: deterministic, and texts sharing words really are close."""
    out = []
    for t in texts:
        v = [0.0] * DIM
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            v[zlib.crc32(w.encode()) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


class FakeReranker:
    """Score = 10 * (fraction of query words found in the passage) - 5, so no overlap scores -5."""
    def predict(self, pairs):
        scores = []
        for q, c in pairs:
            qw = set(re.findall(r"[a-z0-9]+", q.lower()))
            cw = set(re.findall(r"[a-z0-9]+", c.lower()))
            scores.append(10.0 * len(qw & cw) / (len(qw) or 1) - 5.0)
        return scores


class FakeRedis:
    def __init__(self):
        self.store: dict = {}

    def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, ttl):
        return True

    def setex(self, key, ttl, value):
        self.store[key] = value

    def exists(self, key):
        return int(key in self.store)

    def llen(self, key):
        return 0

    def hgetall(self, key):
        return {}

    def ping(self):
        return True


def make_pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 550, 780), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data




def register(client: TestClient, email: str, password: str = "correct-horse-battery") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def upload(client: TestClient, headers: dict, pages: list[str], name: str = "doc.pdf"):
    return client.post("/api/documents", headers=headers,
                       files={"file": (name, make_pdf(pages), "application/pdf")})


def upload_and_index(client: TestClient, headers: dict, pages: list[str], name: str = "doc.pdf") -> str:
    """Upload through the API, then run the Celery task body synchronously."""
    import uuid

    from app.services.ingestion import process_document
    r = upload(client, headers, pages, name)
    assert r.status_code == 202, r.text
    process_document(uuid.UUID(r.json()["id"]))
    return r.json()["id"]
