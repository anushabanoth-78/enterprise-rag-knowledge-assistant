# Interview guide

Answers refer to this repository's actual code. Open the file named in brackets while you explain.

## The ten questions

**1. What is RAG and why did you use it?**
Retrieval-Augmented Generation: fetch the relevant passages first, then have the LLM answer only from them.
An LLM alone does not know a company's private documents and will invent plausible answers. RAG keeps the
knowledge in your database (updatable by uploading a file), and every claim can point to a page.
[`app/services/retrieval.py`, `generation.py`]

**2. Why pgvector instead of a separate vector database?**
One system to deploy, secure and back up. Ownership filtering and joins are ordinary SQL, which matters for
multi-tenant isolation, and vectors are transactional with the rest of the data. The trade-off is scale
(not for billions of vectors) and that HNSW applies `WHERE` filters after the index scan, which I handle with
iterative scan. [`app/db/models.py`, `repository._enable_iterative_scan`]

**3. Vector search vs keyword search?**
Vector search matches meaning ("work from home" ≈ "remote work") but can miss exact tokens such as a policy
number. Postgres full-text search matches exact terms but not paraphrases. I run both. [`repository.vector_search`, `keyword_search`]

**4. What is Reciprocal Rank Fusion?**
Score(item) = Σ 1 / (60 + rank) over each ranked list. It uses only ranks, so I do not have to normalise
cosine similarity against `ts_rank` (different scales). Items both lists like float to the top.
[`retrieval.reciprocal_rank_fusion`, `tests/test_retrieval.py`]

**5. Why Celery and Redis?**
Extracting, embedding and indexing a large PDF takes seconds to minutes. The API returns 202 immediately
and a worker does the work, with retries, a concurrency limit and horizontal scaling. `acks_late` means a
crashed worker's task is redelivered, so the task is idempotent. [`app/worker.py`, `ingestion.process_document`]

**6. What is a cross-encoder reranker?**
A bi-encoder embeds query and passage separately (fast, approximate). A cross-encoder reads them together and
outputs a relevance score (slower, much more precise). I use it only on the ~30 fused candidates.

**7. How do you generate page-level citations?**
PyMuPDF extracts text page by page; the chunker never crosses a page boundary, so every chunk row stores its
exact `page`. The prompt numbers sources `<source id="1" file=… page=…>`; the model writes `[1]`; the server
validates every marker against the retrieved sources and stores the citation list with the message. The UI
fetches the original PDF and opens it at `#page=N`. [`ingestion.chunk_pages`, `generation.extract_citations`, `PdfViewer.tsx`]

**8. How do you prevent hallucinations?**
Three layers: (1) a relevance threshold on the reranker score; if nothing passes, the LLM is never called and
the user gets "I could not find this in your documents"; (2) a prompt that restricts the model to the sources
and requires citations; (3) server-side citation validation, and an answer with no valid citation is marked
`answered=false`. I do not claim zero hallucinations: I measure correct refusals with the benchmark.
[`retrieval.filter_by_score`, `tests/test_chat_api.py::test_threshold_is_what_causes_the_refusal`]

**9. How do you secure multiple users' documents?**
`owner_id` is applied inside every SQL query (documents, chunks, conversations), not just on the final
answer; foreign resources return 404; chunks carry a denormalised `owner_id` so retrieval filters without a
join; storage keys are `{user_id}/{random}.pdf`; the PDF file endpoint is owner-checked. Tests attack routes,
SQL functions and conversations, and I mutation-tested them by deleting the filters. [`tests/test_isolation.py`]

**10. What happens if PDF processing fails?**
Upload-time validation rejects bad files immediately (type, magic bytes, size, pages, corrupt). In the worker,
a permanent failure (scanned/no text, too long) marks the document `failed` with a readable message and is not
retried. A transient failure re-raises so Celery retries with backoff. If the broker is down the document is
kept as `failed` and the user can press Reprocess. [`ingestion.PermanentIngestError`, `tests/test_documents_api.py`]

## Follow-ups to be ready for
* *Draw the two workflows.* See `docs/architecture.md`; practise both diagrams without looking.
* *Why 404 instead of 403?* So an attacker cannot learn whether a document ID exists.
* *Why rewrite follow-up questions?* "And for contractors?" retrieves nothing useful on its own. The rewrite
  falls back to the raw question on any failure so it can never make things worse.
* *What would you change first at 100× scale?* Move embedding to a GPU/batched service, partition or shard
  chunks by tenant, add a stuck-job reaper, stream PDFs instead of buffering them.
* *How would you evaluate it?* `experiments/benchmark.jsonl` with expected source, keywords and
  `expect_answer:false` questions; report hit@k, citation validity, correct refusals, latency, tokens.

## Honest limitations (say these before they ask)
* No OCR: scanned PDFs fail with a clear message. No malware scanning. JWT in `localStorage` (cookie hardening is documented).
* `MIN_RERANK_SCORE` is a starting value, not a tuned one. No accuracy figure is claimed anywhere in this repo.
* Document comparison and a full RAG-evaluation UI beyond the benchmark table are not built.
* The PDF viewer relies on the browser's built-in PDF viewer honouring `#page=N` (Chrome, Edge, Firefox do; some mobile browsers do not).
