"use client";
import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import AuthGuard from "@/components/AuthGuard";
import CitationViewer from "@/components/CitationViewer";
import PdfViewer, { ViewerTarget } from "@/components/PdfViewer";
import { api, Citation, ConversationDetail, Conversation, Doc, Page, streamAsk } from "@/lib/api";

type Msg = {
  key: string; role: "user" | "assistant"; content: string; citations: Citation[];
  streaming?: boolean; answered?: boolean; meta?: string;
};

/** Turn "[1]" markers into buttons. Markers with no matching source stay as plain text. */
function AnswerText({ msg, onCite }: { msg: Msg; onCite: (ref: number) => void }) {
  const refs = new Set(msg.citations.map(c => c.ref));
  return (
    <div className="answer">
      {msg.content.split(/(\[\d+\])/g).map((part, i) => {
        const m = /^\[(\d+)\]$/.exec(part);
        if (m && refs.has(Number(m[1]))) {
          return <button key={i} className="cite" aria-label={`Show source ${m[1]}`} onClick={() => onCite(Number(m[1]))}>[{m[1]}]</button>;
        }
        return <span key={i}>{part}</span>;
      })}
      {msg.streaming && <span className="caret" aria-hidden="true" />}
    </div>
  );
}

function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [provider, setProvider] = useState<"hosted" | "ollama">("hosted");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [activeCite, setActiveCite] = useState<{ key: string; ref: number } | null>(null);
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const loadConvs = useCallback(async () => {
    try { setConvs((await api<Page<Conversation>>("/api/chat/history?limit=50")).items); } catch { /* sidebar is optional */ }
  }, []);

  useEffect(() => {
    loadConvs();
    api<Page<Doc>>("/api/documents?limit=200").then(p => setDocs(p.items.filter(d => d.status === "ready"))).catch(() => undefined);
  }, [loadConvs]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages]);

  async function open(id: string) {
    setError(""); setActiveCite(null);
    try {
      const c = await api<ConversationDetail>(`/api/chat/${id}`);
      setActiveId(id);
      setMessages(c.messages.map(m => ({
        key: `m${m.id}`, role: m.role, content: m.content, citations: m.citations ?? [],
        meta: m.model && m.model !== "none" ? m.model : undefined,
      })));
    } catch (e) { setError((e as Error).message); }
  }

  function newChat() { setActiveId(null); setMessages([]); setError(""); setActiveCite(null); }

  async function remove(id: string) {
    if (!confirm("Delete this conversation?")) return;
    try { await api(`/api/chat/${id}`, { method: "DELETE" }); if (id === activeId) newChat(); loadConvs(); }
    catch (e) { setError((e as Error).message); }
  }

  async function send() {
    const question = input.trim();
    if (question.length < 3 || busy) return;
    setBusy(true); setError(""); setInput("");
    const stamp = Date.now();
    const aKey = `a${stamp}`;
    setMessages(m => [...m,
      { key: `u${stamp}`, role: "user", content: question, citations: [] },
      { key: aKey, role: "assistant", content: "", citations: [], streaming: true }]);
    const patch = (fn: (m: Msg) => Msg) => setMessages(all => all.map(m => (m.key === aKey ? fn(m) : m)));
    try {
      await streamAsk({ question, conversation_id: activeId, provider, top_k: 5,
        document_ids: selected.length ? selected : null }, ev => {
        if (ev.type === "meta") { setActiveId(ev.conversation_id); patch(m => ({ ...m, citations: ev.citations })); }
        else if (ev.type === "token") patch(m => ({ ...m, content: m.content + ev.text }));
        else if (ev.type === "done") patch(m => ({
          ...m, streaming: false, answered: ev.answered,
          meta: ev.model === "none" ? undefined : `${ev.model} · ${Math.round(ev.generation_ms)} ms · ${ev.prompt_tokens + ev.completion_tokens} tokens`,
        }));
        else if (ev.type === "error") throw new Error(ev.message);
      });
      loadConvs();
    } catch (e) {
      setError((e as Error).message);
      setMessages(all => all.filter(m => m.key !== aKey && m.key !== `u${stamp}`));
      setInput(question); // give the question back so nothing is lost
    } finally { setBusy(false); }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  function showCite(key: string, ref: number) {
    setActiveCite({ key, ref });
    document.getElementById(`${key}-${ref}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  return (
    <div className="chat-layout">
      <aside className="panel conv-list" aria-label="Conversations">
        <button onClick={newChat}>New conversation</button>
        {convs.length === 0 && <p className="muted">Your conversations will appear here.</p>}
        <ul>
          {convs.map(c => (
            <li key={c.id} className={c.id === activeId ? "current" : ""}>
              <button className="linklike" onClick={() => open(c.id)} title={c.title}>{c.title}</button>
              <button className="ghost small" aria-label={`Delete conversation ${c.title}`} onClick={() => remove(c.id)}>×</button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="chat-main">
        <h1>Ask your documents</h1>
        <div className="panel row">
          <label className="grow">Model
            <select value={provider} onChange={e => setProvider(e.target.value as "hosted" | "ollama")}>
              <option value="hosted">Hosted API</option>
              <option value="ollama">Local (Ollama)</option>
            </select>
          </label>
          <details className="grow">
            <summary>{selected.length ? `${selected.length} document${selected.length > 1 ? "s" : ""} selected` : "Searching all documents"}</summary>
            <div className="doc-pick">
              {docs.length === 0 && <span className="muted">No indexed documents yet.</span>}
              {docs.map(d => (
                <label key={d.id} className="check">
                  <input type="checkbox" checked={selected.includes(d.id)}
                    onChange={e => setSelected(s => e.target.checked ? [...s, d.id] : s.filter(x => x !== d.id))} />
                  {d.filename}
                </label>
              ))}
            </div>
          </details>
        </div>

        <div className="thread" aria-live="polite">
          {messages.length === 0 && (
            <p className="muted empty">Ask something like “How many annual leave days do employees get?” Answers cite the file and page they came from.</p>
          )}
          {messages.map(m => m.role === "user" ? (
            <div key={m.key} className="bubble user">{m.content}</div>
          ) : (
            <div key={m.key} className="bubble assistant">
              {m.content === "" && m.streaming ? <span className="muted">Searching your documents…</span>
                : <AnswerText msg={m} onCite={ref => showCite(m.key, ref)} />}
              {m.answered === false && <p className="muted">No supporting passages were found, so nothing was guessed.</p>}
              {m.meta && <p className="muted small">{m.meta}</p>}
              {!m.streaming && (
                <CitationViewer citations={m.citations} idPrefix={m.key}
                  active={activeCite?.key === m.key ? activeCite.ref : null}
                  onOpen={c => setViewer({ docId: c.document_id, filename: c.filename, page: c.page })} />
              )}
            </div>
          ))}
          <div ref={bottom} />
        </div>

        {error && <p className="error" role="alert">{error}</p>}
        <div className="composer">
          <textarea rows={2} value={input} onChange={e => setInput(e.target.value)} onKeyDown={onKey}
            placeholder="Ask a question (Enter to send, Shift+Enter for a new line)" aria-label="Your question" disabled={busy} maxLength={2000} />
          <button onClick={send} disabled={busy || input.trim().length < 3}>{busy ? "Answering…" : "Ask"}</button>
        </div>
      </section>
      {viewer && <PdfViewer target={viewer} onClose={() => setViewer(null)} />}
    </div>
  );
}

export default function ChatPage() { return <AuthGuard><Chat /></AuthGuard>; }
