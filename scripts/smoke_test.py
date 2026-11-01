"""End-to-end smoke test against a RUNNING stack (real API, real Celery worker, real database).

    docker compose exec api python scripts/smoke_test.py            # inside the container
    python scripts/smoke_test.py --base http://localhost:8000        # from the host (needs httpx + pymupdf)

It registers two throwaway users, uploads a generated PDF, waits for the worker to index it, asks a
question, checks the page-level citation, checks streaming, and proves the second user cannot see the
first user's document. Exit code is non-zero if any check fails.
"""
import argparse
import json
import sys
import time
import uuid

import fitz
import httpx

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def make_pdf() -> bytes:
    doc = fitz.open()
    pages = ["Welcome page. This handbook describes company policies.",
             "Parking rules. Cars must be registered with reception.",
             "Annual leave policy: employees are eligible for twenty annual leave days per calendar year."]
    for text in pages:
        doc.new_page().insert_textbox(fitz.Rect(50, 50, 550, 780), text, fontsize=11)
    return doc.tobytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--skip-llm", action="store_true", help="skip the checks that need a configured LLM")
    ap.add_argument("--timeout", type=int, default=240, help="seconds to wait for indexing")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base, timeout=120)
    tag = uuid.uuid4().hex[:8]

    r = c.get("/health/ready")
    if not check("API ready (database + redis reachable)", r.status_code == 200, r.text):
        return 1

    def signup(name: str) -> dict:
        r = c.post("/api/auth/register", json={"email": f"{name}-{tag}@example.com", "password": "smoke-test-password"})
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    alice, bob = signup("alice"), signup("bob")
    check("login works", c.post("/api/auth/login", data={"username": f"alice-{tag}@example.com",
                                                          "password": "smoke-test-password"}).status_code == 200)
    check("unauthenticated request is rejected", c.get("/api/documents").status_code == 401)

    pdf = make_pdf()  # generated once: PyMuPDF embeds a random ID, so two calls give different bytes
    r = c.post("/api/documents", headers=alice, files={"file": (f"Handbook-{tag}.pdf", pdf, "application/pdf")})
    check("upload accepted (202, status queued)", r.status_code == 202 and r.json()["status"] == "queued", r.text)
    doc_id = r.json()["id"]
    check("duplicate upload rejected (409)", c.post("/api/documents", headers=alice, files={
        "file": ("again.pdf", pdf, "application/pdf")}).status_code == 409)

    deadline, status = time.time() + a.timeout, "queued"
    while time.time() < deadline and status in ("queued", "processing"):
        time.sleep(2)
        status = c.get(f"/api/documents/{doc_id}/status", headers=alice).json()["status"]
    doc = c.get(f"/api/documents/{doc_id}", headers=alice).json()
    check("worker indexed the document (status ready)", status == "ready", f"status={status} error={doc.get('error')}")
    check("page count preserved", doc["n_pages"] == 3, str(doc["n_pages"]))
    if status != "ready":
        return 1

    check("original PDF downloadable by owner", c.get(f"/api/documents/{doc_id}/file", headers=alice).content[:4] == b"%PDF")

    # --- isolation
    check("other user gets 404 on the document", c.get(f"/api/documents/{doc_id}", headers=bob).status_code == 404)
    check("other user gets 404 on the file", c.get(f"/api/documents/{doc_id}/file", headers=bob).status_code == 404)
    check("other user cannot delete it", c.delete(f"/api/documents/{doc_id}", headers=bob).status_code == 404)
    check("other user's library is empty", c.get("/api/documents", headers=bob).json()["total"] == 0)

    q = {"question": "How many annual leave days do employees get?", "provider": "hosted"}
    if a.skip_llm:
        check("(skipped) LLM answer, streaming and citations", True)
    else:
        r = c.post("/api/chat/ask", headers=alice, json=q)
        ok = r.status_code == 200
        check("ask returns an answer", ok, r.text[:200])
        if ok:
            out = r.json()
            top = out["citations"][0] if out["citations"] else {}
            check("top citation is the leave page (page 3)", top.get("page") == 3 and top.get("document_id") == doc_id, str(top)[:200])
            check("answer cites a valid source marker", "[1]" in out["answer"] and out["answered"], out["answer"][:120])
            cid = out["conversation_id"]

            with c.stream("POST", "/api/chat/ask/stream", headers=alice,
                          json={**q, "question": "How many annual leave days per calendar year?", "conversation_id": cid}) as s:
                events = [json.loads(l[6:]) for l in s.iter_lines() if l.startswith("data: ")]
            kinds = [e["type"] for e in events]
            check("streaming emits meta, tokens, done", kinds[0] == "meta" and "token" in kinds and kinds[-1] == "done", str(kinds[:6]))
            check("streamed answer is grounded (answered=true, citations in meta)",
                  events[-1].get("answered") is True and bool(events[0]["citations"]), str(events[-1]))
            detail = c.get(f"/api/chat/{cid}", headers=alice).json()
            check("conversation persisted with 4 messages and citations",
                  len(detail["messages"]) == 4 and detail["messages"][1]["citations"], str(len(detail["messages"])))

            r = c.post("/api/chat/ask", headers=bob, json={**q, "document_ids": [doc_id]})
            check("other user's question returns no citations from this document",
                  r.status_code == 200 and r.json()["citations"] == [], r.text[:200])
            check("other user cannot open the conversation", c.get(f"/api/chat/{cid}", headers=bob).status_code == 404)
            r = c.post("/api/chat/ask", headers=alice, json={**q, "question": "quantum chromodynamics lagrangian spinor"})
            check("question outside the documents is refused, not invented",
                  r.status_code == 200 and r.json()["answered"] is False, r.text[:200])

    check("delete works", c.delete(f"/api/documents/{doc_id}", headers=alice).status_code == 204)
    check("deleted document is gone", c.get(f"/api/documents/{doc_id}", headers=alice).status_code == 404)
    c.post("/api/auth/logout", headers=alice)
    check("token is revoked after logout", c.get("/api/auth/me", headers=alice).status_code == 401)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
