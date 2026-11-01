"use client";
import { useCallback, useEffect, useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import { api } from "@/lib/api";

type Metrics = {
  users: number; chunks: number; avg_processing_ms: number;
  documents: { total: number; by_status: Record<string, number> };
  questions: { total: number; unanswered: number; avg_ms: number; p95_ms: number; prompt_tokens: number; completion_tokens: number };
  http: { requests: number; errors: number; avg_ms: number; available: boolean };
  workers: { workers_online: number; active_tasks: number; queued_tasks: number | null; reachable: boolean };
};

const Stat = ({ label, value, note }: { label: string; value: string | number; note?: string }) => (
  <div className="stat"><div className="stat-value">{value}</div><div>{label}</div>{note && <div className="muted small">{note}</div>}</div>
);

function Admin() {
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => api<Metrics>("/api/admin/metrics").then(setM).catch(e => setError((e as Error).message)), []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  if (error) return <div className="panel"><h1>Admin</h1><p className="error" role="alert">{error}</p>
    <p className="muted">Only administrators can open this page. Add your email to ADMIN_EMAILS and register again.</p></div>;
  if (!m) return <p className="muted">Loading metrics…</p>;

  const by = m.documents.by_status;
  return (
    <div className="stack">
      <h1>System overview</h1>
      <div className="stats">
        <Stat label="Registered users" value={m.users} />
        <Stat label="Documents" value={m.documents.total} note={`${by.ready ?? 0} indexed`} />
        <Stat label="Failed processing" value={by.failed ?? 0} />
        <Stat label="Questions asked" value={m.questions.total} note={`${m.questions.unanswered} without grounded answer`} />
        <Stat label="Avg answer time" value={`${Math.round(m.questions.avg_ms)} ms`} note={`p95 ${Math.round(m.questions.p95_ms)} ms`} />
        <Stat label="Avg API response" value={m.http.available ? `${m.http.avg_ms} ms` : "n/a"} note={`${m.http.requests} requests, ${m.http.errors} server errors`} />
        <Stat label="Avg indexing time" value={`${Math.round(m.avg_processing_ms)} ms`} note={`${m.chunks} chunks stored`} />
        <Stat label="LLM tokens" value={(m.questions.prompt_tokens + m.questions.completion_tokens).toLocaleString()} note={`${m.questions.prompt_tokens.toLocaleString()} prompt, ${m.questions.completion_tokens.toLocaleString()} completion`} />
      </div>

      <div className="panel stack">
        <strong>Background workers</strong>
        {m.workers.reachable ? (
          <p>{m.workers.workers_online} worker{m.workers.workers_online === 1 ? "" : "s"} online · {m.workers.active_tasks} running · {m.workers.queued_tasks ?? 0} waiting in the queue</p>
        ) : <p className="error">Cannot reach the worker queue.</p>}
        {m.workers.reachable && m.workers.workers_online === 0 && <p className="error">No worker is online, so uploaded documents will stay queued.</p>}
        <strong>Documents by status</strong>
        <div className="row">{Object.entries(by).map(([k, v]) => <span key={k} className={`badge status-${k}`}>{k}: {v}</span>)}
          {Object.keys(by).length === 0 && <span className="muted">No documents yet.</span>}</div>
      </div>
    </div>
  );
}

export default function AdminPage() { return <AuthGuard><Admin /></AuthGuard>; }
