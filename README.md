# Enterprise AI Knowledge Assistant

Upload company PDFs, ask questions in plain language, and get answers that cite the **file and page** they
came from. Built as a multi-user system: every user's documents are isolated, ingestion runs in the
background, and when the documents do not contain the answer the system says so instead of guessing.

**Live demo:** _add your deployed URL here_ · **Demo video:** _add a 1–2 minute recording_ · **Screenshots:** _add to `docs/screenshots/`_

## The problem

A company has hundreds of PDFs: HR policies, handbooks, technical manuals, finance reports. Finding "how many
annual leave days do I get?" means opening several documents and searching manually. This project lets an
employee ask the question and returns the answer with sources:

> Employees are eligible for 20 annual leave days per calendar year [1].
> **Sources:** HR_Policy_2026.pdf, page 12 (click to open that page)

*(Illustrative: real answers depend on the documents you upload.)*

## What makes it more than a "ChatGPT PDF chatbot"

| Basic PDF chatbot | This project |
|---|---|
| One user, one file, in memory | Many users, many documents, PostgreSQL, per-user isolation enforced in SQL and tested |
| Upload blocks until done | Async ingestion: API returns 202, Celery worker indexes in the background, status is visible and retryable |
| Vector search only | Hybrid retrieval: pgvector + Postgres full-text, fused with RRF, then cross-encoder reranking |
| Answers whatever it retrieved | Relevance threshold: if nothing passes, the LLM is **not called** and the user gets "not found" |
| "Trust me" answers | Page-level citations, validated server-side, clickable to open the PDF at that page |
| Single model | Hosted API or local Ollama, streaming responses, side-by-side model comparison with latency and tokens |
| No operations story | Auth + token revocation, rate limits, upload limits, migrations, structured logs, health checks, admin metrics, CI |

## Features

* **Auth:** register/login, JWT with expiry, bcrypt (SHA-256 pre-hash), logout that really revokes the token, per-IP brute-force limits.
* **Documents:** multi-upload, status (`queued → processing → ready | failed`), pagination, search, reprocess, delete (removes chunks, vectors and the stored file), duplicate detection.
* **Retrieval:** vector + full-text + RRF + rerank + threshold, document/user filtering.
* **Q&A:** conversations with history, follow-up questions (rewritten into standalone queries), streaming, citations, model comparison.
* **Admin:** users, documents by status, failed jobs, questions, unanswered rate, latency (avg/p95), API response time, tokens, Celery worker/queue status.
* **Security:** see [docs/architecture.md](docs/architecture.md#security-model).

## Tech stack

Next.js 14 + TypeScript · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 16 + pgvector · Celery + Redis ·
PyMuPDF · sentence-transformers (embeddings + cross-encoder) · any OpenAI-compatible API or Ollama ·
Docker Compose · pytest · Vitest · GitHub Actions.

## How it works

Full diagrams (components, both workflows, ER model, decisions and trade-offs): **[docs/architecture.md](docs/architecture.md)**

```
Ingestion:  upload → validate → store file → queue → worker: extract per page → chunk → embed → index → ready
Question :  query → (rewrite follow-up) → vector ∥ full-text → RRF → rerank → threshold → LLM → validate citations
```

API reference: [docs/api.md](docs/api.md) (Swagger UI at `http://localhost:8000/docs`).
Interview preparation: [docs/interview-guide.md](docs/interview-guide.md).

## Run it on Windows with Docker Desktop

**Prerequisites:** Docker Desktop (WSL 2 backend). Give it **at least 6 GB RAM** (Settings → Resources; or
`memory=6GB` in `%UserProfile%\.wslconfig`): the worker loads two ML models.

```powershell
# 1. get the code, then in the project folder:
copy .env.example .env
notepad .env        # set JWT_SECRET, ADMIN_EMAILS (your email), and HOSTED_API_KEY (see "LLM options")

# 2. build and start everything (first build downloads PyTorch, so allow several minutes)
docker compose up --build

# 3. open the app
#    http://localhost:3000   the application
#    http://localhost:8000/docs   API documentation
```

Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`

1. Open http://localhost:3000, **create an account** with the email you put in `ADMIN_EMAILS` (that account becomes admin).
2. **Library** → upload a PDF → watch it go `queued → processing → ready`. The first document is slower
   because the embedding model downloads once (cached in a Docker volume).
3. **Ask** → type a question → answer streams in with `[1]`-style citations → click **Open page N** to see the source page.

Everyday commands:

```powershell
docker compose logs -f worker          # watch ingestion
docker compose down                    # stop (keeps data)
docker compose down -v                 # stop and DELETE all data (database, uploads, model cache)
docker compose exec api python scripts/smoke_test.py   # end-to-end self-test of the running stack
```

### LLM options

| Option | Setup |
|---|---|
| **Hosted API** (OpenAI or any OpenAI-compatible service) | `HOSTED_API_KEY=…`, optionally `HOSTED_BASE_URL`, `HOSTED_MODEL` in `.env`. Choose "Hosted API" in the UI |
| **Local Ollama** | `docker compose --profile local up`, then `docker compose exec ollama ollama pull llama3.1:8b`. Choose "Local (Ollama)" in the UI. Needs several GB of RAM |
| **No key, just trying the UI** | Run `python scripts/dev_fake_llm.py` on the host and set `HOSTED_BASE_URL=http://host.docker.internal:9100/v1`, `HOSTED_API_KEY=anything`. It quotes the top source: good for demos, not real answers |

If no LLM is configured the API returns a clear 503 ("Hosted LLM is not configured") instead of failing silently.

## Environment variables

All are documented in [`.env.example`](.env.example). The important ones:

| Variable | Purpose |
|---|---|
| `JWT_SECRET` | Signs tokens. Must be random and ≥ 32 chars in production |
| `ADMIN_EMAILS` | Comma-separated emails that become admins when they register |
| `STORAGE_BACKEND` | `local` (Compose volume) or `s3` (Cloudflare R2 / S3 / MinIO) |
| `CORS_ORIGINS` | Exact frontend origin(s) allowed to call the API |
| `MAX_UPLOAD_MB`, `MAX_PAGES_PER_DOCUMENT`, `MAX_DOCUMENTS_PER_USER` | Upload limits |
| `MIN_RERANK_SCORE` | Relevance cut-off behind "not found" answers: **tune it** (below) |
| `HOSTED_*`, `OLLAMA_*` | LLM providers |
| `ENVIRONMENT=production` | Makes the API refuse to start with unsafe config |

## Testing

```powershell
# Frontend (needs Node 20)
cd frontend; npm ci; npm run typecheck; npm test; npm run build

# Backend: needs a PostgreSQL with pgvector. Start a throwaway one:
docker run -d --name pgtest -e POSTGRES_USER=rag -e POSTGRES_PASSWORD=rag -e POSTGRES_DB=rag_test -p 5433:5432 pgvector/pgvector:pg16
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
$env:TEST_DATABASE_URL = "postgresql+psycopg://rag:rag@localhost:5433/rag_test"
pytest -q
```

Without `TEST_DATABASE_URL`, only the unit tests run; the API/isolation tests are skipped. The tests fake
the ML models, the LLM, Redis and Celery, but run **real SQL against real Postgres**. The most important
file is `tests/test_isolation.py`. `scripts/smoke_test.py` is a separate end-to-end check against a running stack.

### Evaluation (measure, don't claim)

Edit `experiments/benchmark.jsonl` with questions about *your* documents (include some with
`"expect_answer": false`), upload those documents, then:

```powershell
docker compose exec api python -m app.services.evaluation --email you@example.com
docker compose cp api:/data/results/results.csv experiments/results.csv
```

It reports hit@k (was the right file retrieved), keyword recall, valid-citation rate, **correct refusals**,
latency and tokens, and the Evaluation page (admin) shows the table. Use the refusal numbers to tune
`MIN_RERANK_SCORE`. No accuracy figure appears in this repository because none has been measured on real
documents yet.

## Deployment

Vercel (frontend) + Railway (API, worker, Redis) + Neon (Postgres + pgvector) + Cloudflare R2 (PDFs):
**[docs/deployment.md](docs/deployment.md)**. In short: `STORAGE_BACKEND=s3` is mandatory for multi-service
hosting, Redis stays private, `ENVIRONMENT=production` enables the startup safety checks, and
`python scripts/smoke_test.py --base https://your-api` verifies the live system.

## Project layout

```
app/            FastAPI app: api/ (routes), core/ (config, security, metrics), db/ (models, repository),
                services/ (ingestion, retrieval, generation, evaluation), storage.py, worker.py
alembic/        database migrations           tests/       pytest suite (+ helpers/fakes)
frontend/       Next.js app (app/, components/, lib/, tests/)
scripts/        smoke_test.py, dev_fake_llm.py   experiments/   benchmark + results
docs/           architecture, API, deployment, interview guide
Dockerfile · docker-compose.yml · .env.example · .github/workflows/ci.yml
```

## Verification status

What was actually run while building this version, and what was **not**:

| Check | Result |
|---|---|
| Backend tests (`pytest`: unit, API, isolation) on real PostgreSQL 16 + pgvector 0.6 | **86 passed, 0 failed** |
| User-isolation tests (10) mutation-checked: deleting the `owner_id` filter from retrieval SQL / `get_document` | tests fail as they should |
| Alembic: upgrade on an empty DB, downgrade/upgrade, `alembic check` (migration matches models) | clean |
| Live stack smoke test: real uvicorn + real Celery worker + real Redis + real Postgres (24 checks: auth, upload, async indexing, page-3 citation, streaming, persistence, isolation, refusal, delete, logout) | **24 passed, 0 failed** |
| Admin metrics against a live Celery worker; CORS allows only the configured frontend origin | verified |
| Frontend: `tsc --noEmit` | clean |
| Frontend unit tests (Vitest: SSE parsing, auth redirect, error handling) | **6 passed, 0 failed** |
| Frontend production build (`next build`) | succeeded (7 routes) |
| `docker-compose.yml` static checks (services, volumes, build contexts, no DB/Redis ports published) | passed |
| **Not run:** `docker compose up/build`, the Docker images | Docker was unavailable where this was built |
| **Not run:** real embedding / cross-encoder models, a real LLM | tests and smoke test use a deterministic stand-in for the models and a fake OpenAI-compatible server |
| **Not run:** the UI in a real browser; pgvector ≥ 0.8 iterative scan; Vercel/Railway/Neon/R2 deployment | the code is written and the fallback path for older pgvector is tested |

So the first `docker compose up --build` on your machine is the first time the real models and images run
together. If something fails, `docker compose logs api worker` will say what.

## Known limitations and future work

* No OCR (scanned PDFs fail with a clear message); no malware scanning.
* `MIN_RERANK_SCORE` default is untuned. A follow-up like "and what else?" can fall below it and be refused.
* JWT is kept in `localStorage`; httpOnly cookies are the hardening step.
* Closing the browser mid-stream leaves an empty conversation entry; PDF `#page=N` depends on the browser's PDF viewer.
* Not built: document-to-document comparison, a stuck-job reaper, per-document sharing, MFA, a RAG evaluation UI beyond the benchmark table.
