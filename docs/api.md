# API reference

Interactive docs (Swagger UI) are served by the running API at `/docs`. All routes except `/health*` need
`Authorization: Bearer <token>`. Errors are JSON: `{"detail": "..."}` (422 validation errors carry a list).

| Method | Path | Purpose | Notes |
|---|---|---|---|
| POST | `/api/auth/register` | Create account, returns token | 409 duplicate email, IP rate-limited |
| POST | `/api/auth/login` | Form login (`username`=email), returns token | IP rate-limited |
| GET | `/api/auth/me` | Current user | includes `is_admin` |
| POST | `/api/auth/logout` | Revoke the token (Redis deny-list) | 204 |
| POST | `/api/documents` | Upload a PDF | 202 queued; 400/413/415/422 validation; 409 duplicate or limit |
| GET | `/api/documents?q=&limit=&offset=` | List own documents | paginated `{items,total,limit,offset}` |
| GET | `/api/documents/{id}` | Document details | 404 if not yours |
| GET | `/api/documents/{id}/status` | DB status + Celery task state | |
| GET | `/api/documents/{id}/file` | Original PDF (owner only) | used by the citation viewer |
| POST | `/api/documents/{id}/reprocess` | Re-run ingestion | 409 while queued/processing |
| DELETE | `/api/documents/{id}` | Delete row, chunks, vectors, stored file | 204 |
| POST | `/api/chat/ask` | Ask a question | creates a conversation if `conversation_id` is omitted |
| POST | `/api/chat/ask/stream` | Same, as server-sent events | `meta` → `token`* → `done` \| `error` |
| POST | `/api/chat/compare` | Two models, same evidence | latency + tokens per model |
| GET | `/api/chat/history` | List own conversations | paginated |
| GET | `/api/chat/{id}` | Conversation with messages + citations | |
| GET | `/api/chat/{id}/messages` | Messages only | `limit`, `offset` |
| DELETE | `/api/chat/{id}` | Delete a conversation | 204 |
| GET | `/api/evaluation/usage` | Own usage per model | |
| GET | `/api/evaluation/results` | Benchmark CSV | admin only |
| GET | `/api/admin/metrics` | System metrics | admin only |
| GET | `/health` | Liveness | |
| GET | `/health/ready` | Readiness (Postgres + Redis) | 503 when degraded |

## Chat request

```json
{ "question": "How many annual leave days are available?",
  "conversation_id": null, "provider": "hosted", "top_k": 5, "rerank": true,
  "document_ids": ["<uuid>", "..."] }
```

`answered: false` means the system refused (nothing relevant retrieved, or the model produced no valid
`[n]` citation). Citations are `{ref, document_id, filename, page, text, score}`; `ref` is the number the
answer uses in `[n]` markers.

## Streaming events

```
data: {"type":"meta","conversation_id":"…","citations":[…],"retrieval_ms":41.2}
data: {"type":"token","text":"Employees "}
data: {"type":"done","answered":true,"model":"gpt-4o-mini","generation_ms":812.4,"prompt_tokens":913,"completion_tokens":57}
data: {"type":"error","message":"LLM provider failed: ConnectError"}
```
