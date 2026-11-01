"use client";
import { FormEvent, useEffect, useState } from "react";
import { api, getToken, json, setToken } from "@/lib/api";

type Mode = "login" | "register";

export default function SignIn() {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (getToken()) window.location.href = "/library"; }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const res = mode === "register"
        ? await api<{ access_token: string }>("/api/auth/register", json({ email, password }))
        : await api<{ access_token: string }>("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({ username: email, password }),
          });
      setToken(res.access_token);
      window.location.href = "/library";
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <h1>Find answers in your company documents</h1>
      <p className="muted">Upload PDFs, ask in plain language, and get answers that point to the exact page.</p>
      <form className="panel stack" onSubmit={submit}>
        <h2 className="h2">{mode === "login" ? "Sign in" : "Create an account"}</h2>
        <label>Email
          <input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} />
        </label>
        <label>Password
          <input type="password" required minLength={8} maxLength={128}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password} onChange={e => setPassword(e.target.value)} />
        </label>
        {mode === "register" && <p className="muted">Use at least 8 characters.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <button disabled={busy}>{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</button>
        <button type="button" className="ghost" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
          {mode === "login" ? "I need an account" : "I already have an account"}
        </button>
      </form>
    </div>
  );
}
