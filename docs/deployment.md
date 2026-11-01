# Deployment: Vercel + Railway + Neon + Cloudflare R2

> These steps follow each provider's general workflow but were **not executed** while building this
> project. Check each provider's current docs, limits and pricing first. Railway and your LLM API can
> incur charges.

Target layout: Next.js on **Vercel**; API, Celery worker and Redis on **Railway** (same Docker image, two
start commands); PostgreSQL + pgvector on **Neon**; PDFs in a private **Cloudflare R2** bucket.

## 1. Neon (database)
1. Create a project and copy the connection string. Convert it to the SQLAlchemy form and keep SSL:
   `postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require`
2. Nothing else to do: the first migration runs `CREATE EXTENSION IF NOT EXISTS vector`.
3. Prefer a pgvector version ≥ 0.8 so `hnsw.iterative_scan` works (older versions still work; see architecture).

## 2. Cloudflare R2 (PDF storage)
1. Create a **private** bucket and an API token with read/write on that bucket.
2. You need: account id, bucket name, access key id, secret access key.
3. `S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com`, `S3_REGION=auto`.

## 3. Railway (API, worker, Redis)
1. New project → add a **Redis** service. Use its *private* URL as `REDIS_URL`. Do not expose Redis publicly.
2. Add a service from your GitHub repo (root directory, uses `Dockerfile`). This is the **API**; the default
   start command applies migrations and starts uvicorn on `$PORT`.
3. Add a second service from the same repo and Dockerfile for the **worker**. Set its start command to:
   `celery -A app.worker.celery_app worker --loglevel=INFO --concurrency=1`
   Keep concurrency low: each task loads the embedding model and the cross-encoder is CPU-heavy.
4. Set these variables on **both** services:

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` (the API then refuses unsafe config) |
| `DATABASE_URL` | Neon string from step 1 |
| `REDIS_URL` | Railway Redis private URL |
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `STORAGE_BACKEND` | `s3` (**required**: Railway services cannot share a disk) |
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION` | from R2 |
| `HOSTED_API_KEY`, `HOSTED_MODEL`, `HOSTED_BASE_URL` | your LLM provider |
| `CORS_ORIGINS` | your exact Vercel URL, e.g. `https://my-app.vercel.app` |
| `ADMIN_EMAILS` | your email |
| `TRUST_PROXY_HEADERS` | `true` (so per-IP login limits see the real client IP behind Railway's proxy) |

5. Generate a public domain for the **API service only**. Check `https://<api-domain>/health/ready`.
6. Ollama is a local-development option; do not host a large local model in the first deployment.

## 4. Vercel (frontend)
1. Import the repo, set **Root Directory** to `frontend`.
2. Environment variable: `NEXT_PUBLIC_API_URL=https://<your-railway-api-domain>` (baked in at build time:
   redeploy after changing it).
3. Add the resulting Vercel URL to the API's `CORS_ORIGINS`, then redeploy the API.

## 5. Verify the live system
```bash
pip install httpx pymupdf
python scripts/smoke_test.py --base https://<your-railway-api-domain>
```
It registers two throwaway users, uploads a PDF, waits for the worker, asks a question, checks the page
citation, checks streaming and proves user isolation.

## Production checklist
- [ ] `ENVIRONMENT=production` and a random `JWT_SECRET`
- [ ] Redis private; bucket private; only the API and frontend have public domains
- [ ] `CORS_ORIGINS` lists only the real frontend
- [ ] Worker concurrency matches available memory
- [ ] Neon backups/branching understood, and a restore tried once
- [ ] `MIN_RERANK_SCORE` tuned on your own documents (see README: Evaluation)
- [ ] LLM provider spend limit set
