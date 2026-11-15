"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {}

export async function api<T = any>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`/api/${path}`, {
    method: init?.method ?? "GET",
    headers: { "content-type": "application/json" },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });
  if (res.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;  // session expired
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? res.statusText));
  return data as T;
}

/** Fetch with loading/error state and optional polling. */
export function useApi<T = any>(path: string | null, pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current) { setData(d); setError(null); }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load();
    const t = pollMs ? setInterval(load, pollMs) : null;
    return () => { alive.current = false; if (t) clearInterval(t); };
  }, [load, pollMs]);
  return { data, error, loading, reload: load };
}

export type Leg = {
  game_id: string; market: string; selection: string; player_id?: string | null; line?: number | null;
  price?: number | null; bookmaker?: string | null; label: string;
};

const KEY = "nbi-slip";
export function useSlip() {
  const [legs, setLegs] = useState<Leg[]>([]);
  useEffect(() => {
    try { setLegs(JSON.parse(localStorage.getItem(KEY) ?? "[]")); } catch { /* storage unavailable */ }
  }, []);
  const save = (next: Leg[]) => {
    setLegs(next);
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
  };
  return {
    legs,
    add: (l: Leg) => save([...legs.filter((x) => legKey(x) !== legKey(l)), l]),
    remove: (i: number) => save(legs.filter((_, j) => j !== i)),
    update: (i: number, patch: Partial<Leg>) => save(legs.map((l, j) => (j === i ? { ...l, ...patch } : l))),
    clear: () => save([]),
  };
}
export const legKey = (l: Leg) => [l.game_id, l.market, l.player_id ?? "", l.selection, l.line ?? ""].join("|");

export const pct = (v: number | null | undefined, d = 1) => (v == null ? "–" : `${(v * 100).toFixed(d)} %`);
export const spct = (v: number | null | undefined, d = 1) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)} %`);
export const num = (v: number | null | undefined, d = 2) => (v == null ? "–" : v.toFixed(d));
export const money = (v: number | null | undefined) => (v == null ? "–" : `${v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`);
export const time = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";
export const age = (s: number | null | undefined) =>
  s == null ? "nie" : s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : s < 172800 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;

export const MARKET: Record<string, string> = {
  points: "Punkte", rebounds: "Rebounds", assists: "Assists", pra: "PRA", threes: "Dreier",
  moneyline: "Moneyline", spread: "Handicap", total: "Over/Under", parlay: "Kombi",
};
export const SEL: Record<string, string> = { over: "Over", under: "Under", home: "Heim", away: "Auswärts" };
export const FLAG: Record<string, string> = {
  few_games: "wenige Spiele", returning_from_absence: "Rückkehr nach Pause", new_team: "neues Team",
  injury_report_stale: "Injury Report veraltet", questionable: "fraglich", probable: "wahrscheinlich",
  low_minutes: "geringe Spielzeit", results_stale: "Ergebnisse veraltet",
};
