export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };
export type Me = { id: string; email: string; is_admin: boolean; created_at: string };
export type Doc = {
  id: string; filename: string; status: string; error: string | null; n_pages: number; n_chunks: number;
  size_bytes: number; processing_ms: number | null; created_at: string;
};
export type Citation = { ref: number; document_id: string; filename: string; page: number; text: string; score: number };
export type Conversation = { id: string; title: string; created_at: string; updated_at: string };
export type ApiMessage = { id: number; role: "user" | "assistant"; content: string; citations: Citation[] | null; model: string | null; created_at: string };
export type ConversationDetail = Conversation & { messages: ApiMessage[] };
export type AskBody = { question: string; conversation_id?: string | null; provider: "hosted" | "ollama"; top_k?: number; rerank?: boolean; document_ids?: string[] | null };
export type CompareAnswer = { provider: string; model: string; answer: string | null; error: string | null; generation_ms: number; prompt_tokens: number; completion_tokens: number; valid_citations: number[] };
export type CompareResult = { question: string; retrieval_ms: number; citations: Citation[]; results: CompareAnswer[] };

export type StreamEvent =
  | { type: "meta"; conversation_id: string; citations: Citation[]; retrieval_ms: number }
  | { type: "token"; text: string }
  | { type: "done"; answered: boolean; model: string; generation_ms: number; prompt_tokens: number; completion_tokens: number }
  | { type: "error"; message: string };

// The JWT lives in localStorage: simple, but readable by any script on the page (XSS). The production
// alternative is an httpOnly, SameSite cookie set by the API. See docs/architecture.md.
export const getToken = () => (typeof window === "undefined" ? null : localStorage.getItem("token"));
export const setToken = (t: string | null) => (t ? localStorage.setItem("token", t) : localStorage.removeItem("token"));

async function errorMessage(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  const d = body?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e: { msg?: string }) => e.msg).filter(Boolean).join("; ") || `Request failed (${res.status})`;
  return `Request failed (${res.status})`;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API}${path}`, { ...init, headers });
  const isAuthCall = path.startsWith("/api/auth/login") || path.startsWith("/api/auth/register");
  if (res.status === 401 && !isAuthCall) {
    setToken(null);
    if (typeof window !== "undefined") window.location.href = "/";
  }
  return res;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await request(path, init);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.status === 204 ? (undefined as T) : res.json();
}

export async function apiBlob(path: string): Promise<Blob> {
  const res = await request(path);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.blob();
}

export const json = (body: unknown, method = "POST"): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export async function logout() {
  try { await request("/api/auth/logout", { method: "POST" }); } catch { /* token is dropped locally either way */ }
  setToken(null);
  window.location.href = "/";
}

/** POST /api/chat/ask/stream and call onEvent for each server-sent event. */
export async function streamAsk(body: AskBody, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) {
  const res = await request("/api/chat/ask/stream", { ...json(body), signal });
  if (!res.ok || !res.body) throw new Error(await errorMessage(res));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find(l => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as StreamEvent);
    }
  }
}
