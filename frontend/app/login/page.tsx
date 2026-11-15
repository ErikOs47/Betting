"use client";
import { useState } from "react";

export default function Login() {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    const res = await fetch("/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ password: pw }) });
    if (res.ok) {
      const next = new URLSearchParams(window.location.search).get("next");
      window.location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    } else {
      setErr((await res.json().catch(() => ({}))).detail ?? "Anmeldung fehlgeschlagen");
      setBusy(false);
    }
  };
  return (
    <div className="pt-safe flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-line bg-surface p-6">
        <img src="/icons/icon-192.png" alt="" width={56} height={56} className="mb-4 rounded-xl" />
        <h1 className="text-lg font-semibold">NBA Betting Intelligence</h1>
        <p className="mb-5 text-sm text-ink-3">Bitte mit dem App-Passwort anmelden.</p>
        <label className="grid gap-1 text-sm">Passwort
          <input type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} className="w-full" />
        </label>
        {err && <p role="alert" className="mt-3 text-sm text-bad">{err}</p>}
        <button type="submit" disabled={busy || !pw} className="mt-5 w-full rounded-md bg-s1 px-3 py-2.5 text-sm font-semibold text-white disabled:opacity-40">{busy ? "Anmelden …" : "Anmelden"}</button>
      </form>
    </div>
  );
}
