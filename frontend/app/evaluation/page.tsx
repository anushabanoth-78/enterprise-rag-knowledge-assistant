"use client";
import { useEffect, useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import { api } from "@/lib/api";

type Row = Record<string, string>;
type Usage = { provider: string; model: string; queries: number; avg_retrieval_ms: number; avg_generation_ms: number; prompt_tokens: number; completion_tokens: number };

const avg = (rows: Row[], k: string) => (rows.length ? rows.reduce((s, r) => s + Number(r[k] || 0), 0) / rows.length : 0);
const pct = (x: number) => `${(x * 100).toFixed(0)}%`;

function Evaluation() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [usage, setUsage] = useState<Usage[]>([]);
  const [denied, setDenied] = useState("");

  useEffect(() => {
    api<Usage[]>("/api/evaluation/usage").then(setUsage).catch(() => undefined);
    api<Row[]>("/api/evaluation/results").then(setRows).catch(e => setDenied((e as Error).message));
  }, []);

  const groups: Record<string, Row[]> = {};
  (rows ?? []).forEach(r => { (groups[`${r.provider} · ${r.model} · rerank=${r.rerank}`] ??= []).push(r); });

  return (
    <div className="stack">
      <h1>Evaluation</h1>
      <div className="panel stack">
        <strong>Your usage</strong>
        {usage.length === 0 ? <p className="muted">No questions asked yet.</p> : (
          <div className="scroll-x"><table>
            <thead><tr><th>Model</th><th>Questions</th><th>Avg retrieval</th><th>Avg generation</th><th>Tokens</th></tr></thead>
            <tbody>{usage.map(u => <tr key={`${u.provider}${u.model}`}><td>{u.provider} · {u.model}</td><td>{u.queries}</td><td>{u.avg_retrieval_ms} ms</td><td>{u.avg_generation_ms} ms</td><td>{(u.prompt_tokens + u.completion_tokens).toLocaleString()}</td></tr>)}</tbody>
          </table></div>)}
      </div>

      <div className="panel stack">
        <strong>Benchmark runs</strong>
        <p className="muted">Run from the server: <code>docker compose exec api python -m app.services.evaluation --email you@example.com</code>. Measured numbers only appear here after a real run.</p>
        {denied ? <p className="muted">{denied}</p> : rows === null ? <p className="muted">Loading…</p> : Object.keys(groups).length === 0 ? <p className="muted">No benchmark results yet.</p> : (
          <div className="scroll-x"><table>
            <thead><tr><th>Configuration</th><th>Questions</th><th>Source found (hit@k)</th><th>Keyword recall</th><th>Valid citations</th><th>Correct refusals</th><th>Avg generation</th></tr></thead>
            <tbody>{Object.entries(groups).map(([name, rs]) => {
              const neg = rs.filter(r => r.expect_answer === "0");
              return <tr key={name}><td>{name}</td><td>{rs.length}</td><td>{pct(avg(rs, "hit_at_k"))}</td><td>{pct(avg(rs, "keyword_recall"))}</td>
                <td>{pct(avg(rs, "citation_valid"))}</td><td>{neg.length ? pct(avg(neg, "refused")) : "n/a"}</td><td>{Math.round(avg(rs, "generation_ms"))} ms</td></tr>;
            })}</tbody>
          </table></div>)}
      </div>
    </div>
  );
}

export default function EvaluationPage() { return <AuthGuard><Evaluation /></AuthGuard>; }
