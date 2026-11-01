"use client";
import { useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import CitationViewer from "@/components/CitationViewer";
import PdfViewer, { ViewerTarget } from "@/components/PdfViewer";
import { api, CompareResult, json } from "@/lib/api";

function Compare() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<CompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);

  async function run() {
    setBusy(true); setError(""); setResult(null);
    try { setResult(await api<CompareResult>("/api/chat/compare", json({ question, providers: ["hosted", "ollama"], top_k: 5 }))); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="stack">
      <h1>Compare models</h1>
      <p className="muted">Both models answer from the same retrieved passages, so differences come from the model, not the search.</p>
      <div className="panel stack">
        <textarea rows={2} value={question} onChange={e => setQuestion(e.target.value)} maxLength={2000} aria-label="Question to compare" placeholder="Type a question" />
        <button onClick={run} disabled={busy || question.trim().length < 3}>{busy ? "Asking both models…" : "Compare"}</button>
        {error && <p className="error" role="alert">{error}</p>}
      </div>
      {result && (<>
        <div className="split">
          {result.results.map(r => (
            <div className="panel stack" key={r.provider}>
              <strong>{r.provider === "hosted" ? "Hosted API" : "Local (Ollama)"}{r.model && ` · ${r.model}`}</strong>
              {r.error ? <p className="error">This model failed: {r.error}</p> : <div className="answer">{r.answer}</div>}
              {!r.error && <p className="muted small">
                {Math.round(r.generation_ms)} ms · {r.prompt_tokens} prompt + {r.completion_tokens} completion tokens ·
                cites {r.valid_citations.length ? r.valid_citations.map(n => `[${n}]`).join(" ") : "no valid sources"}
              </p>}
            </div>
          ))}
        </div>
        <div className="panel">
          <strong>Shared sources</strong> <span className="muted">(retrieved in {Math.round(result.retrieval_ms)} ms)</span>
          {result.citations.length === 0 && <p className="muted">No relevant passages were found in your documents.</p>}
          <CitationViewer citations={result.citations} active={null} idPrefix="cmp"
            onOpen={c => setViewer({ docId: c.document_id, filename: c.filename, page: c.page })} />
        </div>
      </>)}
      {viewer && <PdfViewer target={viewer} onClose={() => setViewer(null)} />}
    </div>
  );
}

export default function ComparePage() { return <AuthGuard><Compare /></AuthGuard>; }
