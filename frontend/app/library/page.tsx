"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import { api, Doc, Page } from "@/lib/api";
import { API, getToken } from "@/lib/api";

const PAGE_SIZE = 10;
const ACTIVE = ["queued", "processing"];

const fmtSize = (b: number) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

function Library() {
  const [page, setPage] = useState<Page<Doc> | null>(null);
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (query) params.set("q", query);
      setPage(await api<Page<Doc>>(`/api/documents?${params}`));
    } catch (e) { setError((e as Error).message); }
  }, [offset, query]);

  useEffect(() => { load(); }, [load]);
  // Debounce the search box so we don't query on every keystroke.
  useEffect(() => { const t = setTimeout(() => { setOffset(0); setQuery(q.trim()); }, 300); return () => clearTimeout(t); }, [q]);
  // Poll while anything is still being indexed by the Celery worker.
  const busy = page?.items.some(d => ACTIVE.includes(d.status));
  useEffect(() => { if (!busy) return; const t = setInterval(load, 2500); return () => clearInterval(t); }, [busy, load]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true); setError("");
    const problems: string[] = [];
    for (const file of Array.from(files)) {
      const body = new FormData();
      body.append("file", file);
      // multipart upload: let the browser set the Content-Type boundary
      const res = await fetch(`${API}/api/documents`, { method: "POST", body, headers: { Authorization: `Bearer ${getToken()}` } });
      if (res.status === 401) { window.location.href = "/"; return; }
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))).detail;
        problems.push(`${file.name}: ${typeof d === "string" ? d : `upload failed (${res.status})`}`);
      }
    }
    if (fileInput.current) fileInput.current.value = "";
    setUploading(false);
    if (problems.length) setError(problems.join("\n"));
    setOffset(0); load();
  }

  async function act(fn: () => Promise<unknown>) {
    setError("");
    try { await fn(); await load(); } catch (e) { setError((e as Error).message); }
  }

  const total = page?.total ?? 0;
  return (
    <div className="stack">
      <h1>Document library</h1>
      <div className="panel stack">
        <label>Upload PDFs
          <input ref={fileInput} type="file" accept="application/pdf,.pdf" multiple disabled={uploading}
            onChange={e => upload(e.target.files)} />
        </label>
        <p className="muted">{uploading ? "Uploading…" : "PDF only. Text is extracted in the background, so large files may take a minute."}</p>
        {error && <p className="error pre" role="alert">{error}</p>}
      </div>

      <div className="panel stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <input style={{ maxWidth: 320 }} type="search" placeholder="Search by file name" aria-label="Search documents"
            value={q} onChange={e => setQ(e.target.value)} />
          <span className="muted">{total} document{total === 1 ? "" : "s"}</span>
        </div>
        {page && page.items.length === 0 ? (
          <p className="muted">{query ? "No documents match that name." : "No documents yet. Upload a PDF above to get started."}</p>
        ) : (
          <div className="scroll-x">
            <table>
              <thead><tr><th>Name</th><th>Status</th><th>Pages</th><th>Size</th><th>Uploaded</th><th /></tr></thead>
              <tbody>
                {page?.items.map(d => (
                  <tr key={d.id}>
                    <td>{d.filename}</td>
                    <td>
                      <span className={`badge status-${d.status}`}>{d.status}</span>
                      {d.error && <div className="error small">{d.error}</div>}
                    </td>
                    <td>{d.n_pages || "–"}</td>
                    <td>{fmtSize(d.size_bytes)}</td>
                    <td>{new Date(d.created_at).toLocaleDateString()}</td>
                    <td className="row">
                      {(d.status === "failed" || d.status === "ready") &&
                        <button className="ghost small" onClick={() => act(() => api(`/api/documents/${d.id}/reprocess`, { method: "POST" }))}>Reprocess</button>}
                      <button className="ghost small danger" onClick={() => confirm(`Delete ${d.filename}? Its indexed text is removed too.`) && act(() => api(`/api/documents/${d.id}`, { method: "DELETE" }))}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {total > PAGE_SIZE && (
          <div className="row pager">
            <button className="ghost small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
            <span className="muted">{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
            <button className="ghost small" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function LibraryPage() { return <AuthGuard><Library /></AuthGuard>; }
