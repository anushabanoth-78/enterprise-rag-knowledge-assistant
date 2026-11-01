# 📚 Enterprise AI Knowledge Assistant

Ask questions about your PDFs and get answers that **cite the file and page** they came from. Built with **hybrid retrieval (pgvector + full-text)**, **cross-encoder reranking**, **FastAPI + Celery**, a **Next.js** frontend and **Docker Compose**.

---
<a id="table-of-contents"></a>

## Table of Contents

- [Overview](#overview)
- [Demo](#demo)
- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the App](#running-the-app)
- [How Retrieval Works](#how-retrieval-works)
- [Choosing an LLM](#choosing-an-llm)
- [Troubleshooting](#troubleshooting)
- [Technologies Used](#technologies-used)
- [Limitations](#limitations)
- [Future Improvements](#future-improvements)
- [Project Author](#project-author)

<a id="overview"></a>
## 🔍 Overview

Finding one answer in many PDFs (policies, handbooks, resumes, reports) means opening each file and searching by hand. This project lets a user upload PDFs, ask a question in plain language, and read a short answer with numbered sources:

> The student holds a B.Tech in Computer Science and Engineering from IIT Goa [1].
> **Source 1:** Resume.pdf, page 1

*(Illustrative example. Real answers depend on the documents you upload.)*

It combines:

- **Hybrid retrieval**: pgvector similarity search + PostgreSQL full-text search, merged with Reciprocal Rank Fusion
- **Cross-encoder reranking** (sentence-transformers, running on PyTorch)
- **A relevance threshold**: if no passage is good enough, the LLM is *not called* and the user gets "I could not find this in your documents."
- **Page-level citations**, checked on the server
- **Async ingestion** with Celery and Redis

⬆️ [Back to Top](#table-of-contents)

<a id="demo"></a>
## 🎬 Demo

**Live demo:** _add your deployed URL here_

▶️ **Demo video:** _add your 1–2 minute recording link here_

### Screenshots

Add your own screenshots to `docs/screenshots/` and link them here. Use your own documents only.

| Ask page (cited answer) | Compare models page |
|---|---|
| `![Ask](docs/screenshots/ask.png)` | `![Compare](docs/screenshots/compare.png)` |

> **Privacy tip:** do not show other people's resumes or personal data in screenshots. Use sample documents.

⬆️ [Back to Top](#table-of-contents)

<a id="features"></a>
## ✨ Features

✓ Register / login with JWT, per-user document isolation  
✓ Multi-document upload with status: `queued → processing → ready | failed`  
✓ Background ingestion (API returns `202`, worker indexes the file)  
✓ Hybrid search + reranking + relevance cutoff  
✓ Streaming answers with `[1]`-style citations and "Open page" links  
✓ Select one or several documents to search; multi-document questions keep passages from each file  
✓ **Compare models** page: two models answer from the same retrieved passages  
✓ Hosted LLM (any OpenAI-compatible API, tested with Groq) or local Ollama  
✓ Rate limits, upload limits, health checks, structured logs, Alembic migrations

⬆️ [Back to Top](#table-of-contents)

<a id="architecture"></a>
## 🧩 Architecture

```text
Browser (Next.js, :3000)
        │
        ▼
FastAPI (:8000) ───────► PostgreSQL + pgvector   (users, documents, chunks, vectors, chats)
        │
        ├──► Redis ◄──── Celery worker   (extract pages → chunk → embed → index)
        │
        └──► LLM: hosted OpenAI-compatible API (e.g. Groq)  or  Ollama (optional)
```

```text
Ingestion : upload → validate → store file → queue → worker: extract per page → chunk → embed → index → ready
Question  : query → (rewrite follow-up) → vector ∥ full-text → RRF → rerank → cutoff → LLM → validate citations
```

⬆️ [Back to Top](#table-of-contents)

<a id="project-structure"></a>
## 📂 Project Structure

```text
enterprise-rag-knowledge-assistant/
│
├── app/                    # FastAPI backend
│   ├── api/                # routes (auth, documents, chat)
│   ├── core/               # config, security, metrics
│   ├── db/                 # models, repository (SQL)
│   ├── services/           # ingestion, retrieval, generation, evaluation
│   ├── storage.py
│   └── worker.py           # Celery tasks
│
├── alembic/                # database migrations
├── frontend/               # Next.js app
├── experiments/            # benchmark questions and results
├── scripts/                # smoke test, helpers
├── tests/
├── docs/                   # screenshots, extra docs
│
├── Dockerfile
├── docker-compose.yml
├── requirements-base.txt
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

⬆️ [Back to Top](#table-of-contents)

<a id="installation"></a>
## ⚙️ Installation

### Prerequisites

- Docker Desktop (WSL 2 backend) or Docker Engine on Linux
- **At least 4 GB RAM for Docker/WSL** (6 GB is safer). The API and worker load PyTorch, an embedding model and a reranker.
- A free API key from an OpenAI-compatible provider (for example Groq)

### Steps

```bash
git clone https://github.com/anushabanoth-78/enterprise-rag-knowledge-assistant.git
cd enterprise-rag-knowledge-assistant

cp .env.example .env
nano .env            # fill in the values described under "Configuration"
```

### PyTorch note

The Dockerfile installs the CPU build of PyTorch. If the download from `download.pytorch.org` keeps dropping on your network, download the wheel once on your machine and let Docker copy it:

```bash
mkdir -p wheels
wget -c https://download.pytorch.org/whl/cpu/torch-2.4.1%2Bcpu-cp311-cp311-linux_x86_64.whl -P wheels/
```

Then use these two lines in the `Dockerfile` instead of the `pip install torch ... --index-url` line:

```dockerfile
COPY wheels/ /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/torch-2.4.1+cpu-cp311-cp311-linux_x86_64.whl
```

(`wheels/` is in `.gitignore`, so the 186 MB file is never committed.)

⬆️ [Back to Top](#table-of-contents)

<a id="configuration"></a>
## 🔧 Configuration

Never commit `.env`. The important variables:

| Variable | Purpose |
|---|---|
| `JWT_SECRET` | Signs login tokens. Use a long random value (`openssl rand -hex 32`) |
| `ADMIN_EMAILS` | Emails that become admins when they register |
| `CORS_ORIGINS` | Frontend origin(s) allowed to call the API |
| `HOSTED_BASE_URL` | OpenAI-compatible endpoint, e.g. `https://api.groq.com/openai/v1` |
| `HOSTED_API_KEY` | Your provider key. **Keep it private** |
| `HOSTED_MODEL` | A model your provider offers, e.g. `openai/gpt-oss-120b` |
| `MIN_RERANK_SCORE` | Relevance cutoff behind "not found" answers (see below) |
| `MAX_UPLOAD_MB`, `MAX_DOCUMENTS_PER_USER` | Upload limits |
| `OLLAMA_URL`, `OLLAMA_MODEL` | Only for the optional local model |

**Tuning `MIN_RERANK_SCORE`:** cross-encoder scores are often negative. A cutoff that is too high refuses questions it could answer; too low lets weak passages through. This project runs with `-13` on its test documents. Start there, then adjust: raise it if off-topic sources appear, lower it if valid questions return "not found".

⬆️ [Back to Top](#table-of-contents)

<a id="running-the-app"></a>
## ▶️ Running the App

```bash
docker compose up -d --build
docker compose ps
```

Wait about a minute (the models load on the first request), then open:

| Page | URL |
|---|---|
| App | http://localhost:3000 |
| Ask | http://localhost:3000/chat |
| Library | http://localhost:3000/library |
| Compare models | http://localhost:3000/compare |
| API docs | http://localhost:8000/docs |

1. Create an account.
2. **Library** → upload a PDF and wait for `ready`.
3. **Ask** → tick the document(s), type a question, read the answer and click **Open page** on a source.

### Everyday commands

```bash
docker compose logs -f api worker     # watch logs
docker compose down                   # stop (keeps data)
docker compose down -v                # stop and DELETE all data (database, uploads, model cache)
```

After editing code, rebuild: `docker compose build api worker && docker compose up -d --force-recreate api worker`. After editing only `.env`, the `up -d --force-recreate` part is enough.

⬆️ [Back to Top](#table-of-contents)

<a id="how-retrieval-works"></a>
## 🧠 How Retrieval Works

1. **Hybrid search**: pgvector similarity and PostgreSQL full-text search each return 30 candidates.
2. **Fusion**: Reciprocal Rank Fusion (`k = 60`) merges the two rankings.
3. **Rerank**: a cross-encoder scores each candidate against the question.
4. **Cutoff**: passages below `MIN_RERANK_SCORE` are dropped. If none remain, the app answers "I could not find this in your documents" without calling the LLM.
5. **Per-document balance**: when several documents are selected, the best passages of each file are kept first, so one file cannot crowd out the others.
6. **Top 8** passages go to the LLM, which must cite them as `[1]`, `[2]`.
7. **Citation check**: the server keeps only citation numbers that match a real source.

> **Tip:** name the people or topics in multi-document questions. "Compare the education of A and B" retrieves better than "compare both students".

⬆️ [Back to Top](#table-of-contents)

<a id="choosing-an-llm"></a>
## 🤖 Choosing an LLM

| Option | Setup |
|---|---|
| **Hosted API** (recommended) | Set `HOSTED_BASE_URL`, `HOSTED_API_KEY`, `HOSTED_MODEL` in `.env`. Fast, small memory footprint |
| **Local Ollama** (optional) | `docker compose --profile local up -d`, then `docker compose exec ollama ollama pull llama3.2:3b` and set `OLLAMA_MODEL=llama3.2:3b`. Needs several extra GB of RAM and is slow on CPU |

Some models write citations in their own style (for example `【1】`). The backend converts those to plain `[1]` in both normal and streaming responses.

⬆️ [Back to Top](#table-of-contents)

<a id="troubleshooting"></a>
## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| Docker build fails while downloading PyTorch (`BrokenPipeError`) | Download the wheel on the host and `COPY` it (see [Installation](#installation)) |
| Frontend build fails with a `@next/swc` / `ld-linux` error on Alpine | Use `node:20-slim` instead of `node:20-alpine`, and `groupadd`/`useradd` instead of `addgroup`/`adduser` |
| "Invalid Auth key" (HTTP 400/401) | The provider rejected the key. Test it with `curl` against `/chat/completions` before putting it in `.env` |
| "model not found" (HTTP 404) | List available models: `curl -s $HOSTED_BASE_URL/models -H "Authorization: Bearer $KEY"` |
| Answers say "could not find" with 0 tokens | Retrieval returned nothing: lower `MIN_RERANK_SCORE`, check the document is `ready`, or wait for the models to warm up |
| `Local (Ollama)` shows `ConnectError` | The `ollama` container is not running. Start with `--profile local` or ignore it |
| Out-of-memory / very slow | Give Docker/WSL more RAM (`memory=6GB` in `.wslconfig`), or stop Ollama |

⬆️ [Back to Top](#table-of-contents)

<a id="technologies-used"></a>
## 🧰 Technologies Used

- **Python 3.11**: FastAPI, SQLAlchemy 2, Alembic, Celery, httpx
- **PyTorch + sentence-transformers**: embeddings (`all-MiniLM-L6-v2`) and cross-encoder reranking (pre-trained models, used for inference)
- **PostgreSQL 16 + pgvector**: vectors and full-text search
- **Redis**: Celery broker
- **PyMuPDF**: page-level text extraction
- **Next.js 14 + TypeScript**: frontend
- **Docker Compose**: runs the whole stack
- **Groq** (hosted) or **Ollama** (local) for answer generation

⬆️ [Back to Top](#table-of-contents)

<a id="limitations"></a>
## ⚠️ Limitations

- No OCR: scanned (image-only) PDFs produce little or no text.
- No accuracy figures are published here, because they have not been measured on a benchmark yet. The repository includes an evaluation script in `experiments/`.
- `MIN_RERANK_SCORE` needs tuning per document set.
- Name or heading questions ("what is the name?") match weakly, because a name is usually just a title line.
- Sign-ups are open, so a public deployment should limit registration to protect your LLM quota.

⬆️ [Back to Top](#table-of-contents)

<a id="future-improvements"></a>
## 🚀 Future Improvements

- OCR for scanned PDFs
- A measured retrieval and citation benchmark with published results
- Per-document sharing between users
- httpOnly cookie sessions instead of `localStorage` tokens
- Upgrade Next.js to a patched release before any public deployment
- Public deployment with HTTPS (VPS + Caddy)

⬆️ [Back to Top](#table-of-contents)

<a id="project-author"></a>
## 👩‍💻 Project Author

**Banoth Anusha**  
B.Tech, Computer Science & Engineering: Indian Institute of Technology Goa

Interested in machine learning, NLP and building end-to-end applications. This project shows a full pipeline: ingestion, hybrid retrieval, reranking, LLM integration, containerised deployment.

🔗 GitHub: https://github.com/anushabanoth-78

⬆️ [Back to Top](#table-of-contents)
