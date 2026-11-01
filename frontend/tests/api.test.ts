import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, streamAsk, StreamEvent } from "@/lib/api";

const sse = (events: object[]) => events.map(e => `data: ${JSON.stringify(e)}\n\n`).join("");

function streamResponse(chunks: string[], status = 200) {
  const enc = new TextEncoder();
  const body = new ReadableStream({
    start(c) { chunks.forEach(ch => c.enqueue(enc.encode(ch))); c.close(); },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

beforeEach(() => {
  const store: Record<string, string> = { token: "jwt-123" };
  vi.stubGlobal("window", { location: { href: "" } });
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("streamAsk", () => {
  it("parses events even when frames are split across network chunks", async () => {
    const full = sse([
      { type: "meta", conversation_id: "c1", citations: [], retrieval_ms: 5 },
      { type: "token", text: "Twenty " }, { type: "token", text: "days [1]." },
      { type: "done", answered: true, model: "m", generation_ms: 1, prompt_tokens: 1, completion_tokens: 1 },
    ]);
    const cut = 37; // splits the first frame in half
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse([full.slice(0, cut), full.slice(cut)])));
    const seen: StreamEvent[] = [];
    await streamAsk({ question: "How many days?", provider: "hosted" }, e => seen.push(e));
    expect(seen.map(e => e.type)).toEqual(["meta", "token", "token", "done"]);
    expect(seen.filter(e => e.type === "token").map(e => (e as { text: string }).text).join("")).toBe("Twenty days [1].");
  });

  it("sends the bearer token and JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamResponse([]));
    vi.stubGlobal("fetch", fetchMock);
    await streamAsk({ question: "hello there", provider: "ollama" }, () => undefined);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/chat\/ask\/stream$/);
    expect((init.headers as Headers).get("Authorization")).toBe("Bearer jwt-123");
    expect(JSON.parse(init.body).provider).toBe("ollama");
  });

  it("surfaces the API error message for non-200 responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Conversation not found" }), { status: 404 })));
    await expect(streamAsk({ question: "hello there", provider: "hosted" }, () => undefined)).rejects.toThrow("Conversation not found");
  });
});

describe("api", () => {
  it("clears the token and redirects to sign-in on 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));
    await expect(api("/api/documents")).rejects.toThrow();
    expect(localStorage.getItem("token")).toBeNull();
    expect((window as unknown as { location: { href: string } }).location.href).toBe("/");
  });

  it("does not redirect on a failed login (wrong password is a form error)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Incorrect email or password" }), { status: 401 })));
    await expect(api("/api/auth/login", { method: "POST" })).rejects.toThrow("Incorrect email or password");
    expect(localStorage.getItem("token")).toBe("jwt-123");
  });

  it("joins validation errors from FastAPI's 422 format", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ msg: "too short" }, { msg: "bad email" }] }), { status: 422 })));
    await expect(api("/api/auth/register", { method: "POST" })).rejects.toThrow("too short; bad email");
  });
});
