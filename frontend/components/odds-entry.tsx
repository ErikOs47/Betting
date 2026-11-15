"use client";
import { useState } from "react";
import { Badge, Button, Card, Notice } from "@/components/ui";
import { MARKET, SEL, api, time, useApi } from "@/lib/api";

type Prop = { player_id: string; market: string; line: string; a: string; b: string };
const PM = ["points", "rebounds", "assists", "threes", "pra"];
const n = (v: string) => Number(v.replace(",", "."));

/** Enter a bookmaker's quotes by hand (e.g. Tipico). Both sides are needed so the margin can be removed. */
export function OddsEntry({ gameId, game, players, onSaved }: { gameId: string; game: any; players: any[]; onSaved?: () => void }) {
  const [book, setBook] = useState("tipico");
  const [ml, setMl] = useState({ a: "", b: "" });
  const [sp, setSp] = useState({ line: "", a: "", b: "" });
  const [to, setTo] = useState({ line: "", a: "", b: "" });
  const [props, setProps] = useState<Prop[]>([{ player_id: players[0]?.player_id ?? "", market: "points", line: "", a: "", b: "" }]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const stored = useApi<any>(`odds/${gameId}`);
  const setProp = (i: number, patch: Partial<Prop>) => setProps(props.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const save = async () => {
    const quotes: any[] = [];
    if (ml.a && ml.b) quotes.push({ market: "moneyline", price_a: n(ml.a), price_b: n(ml.b) });
    if (sp.line && sp.a && sp.b) quotes.push({ market: "spread", line: n(sp.line), price_a: n(sp.a), price_b: n(sp.b) });
    if (to.line && to.a && to.b) quotes.push({ market: "total", line: n(to.line), price_a: n(to.a), price_b: n(to.b) });
    for (const p of props) if (p.player_id && p.line && p.a && p.b) quotes.push({ market: p.market, player_id: p.player_id, line: n(p.line), price_a: n(p.a), price_b: n(p.b) });
    if (!quotes.length) { setMsg({ ok: false, text: "Bitte mindestens einen Markt vollständig ausfüllen (beide Quoten)." }); return; }
    try {
      const r = await api("odds/manual", { method: "POST", body: { game_id: gameId, bookmaker: book.trim().toLowerCase(), quotes } });
      setMsg({ ok: true, text: `${r.stored / 2} Märkte gespeichert (${r.bookmaker}). Der Value Finder vergleicht sie jetzt mit dem Modell.` });
      stored.reload(); onSaved?.();
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };
  const cell = "w-20";
  return (
    <Card title="Buchmacherquoten erfassen (z. B. Tipico)" right={<Button kind="primary" onClick={save}>Quoten speichern</Button>}>
      <p className="mb-3 text-xs text-ink-3">Quoten aus der Buchmacher-App abtippen. Sie werden mit Zeitstempel gespeichert und im Value Finder wie API-Quoten ausgewertet; ein späteres erneutes Erfassen liefert die Schlussquote für den CLV. Immer beide Seiten eintragen, sonst kann die Marge nicht entfernt werden.</p>
      <div className="space-y-3 text-sm">
        <label className="flex items-center gap-2">Buchmacher<input className="w-32" value={book} onChange={(e) => setBook(e.target.value)} aria-label="Buchmacher" /></label>
        <div className="grid gap-2">
          <div className="flex flex-wrap items-center gap-2"><span className="w-28 text-ink-2">Moneyline</span><span className="w-20" />
            <input className={cell} inputMode="decimal" placeholder={game.home} aria-label="Moneyline Heim" value={ml.a} onChange={(e) => setMl({ ...ml, a: e.target.value })} />
            <input className={cell} inputMode="decimal" placeholder={game.away} aria-label="Moneyline Auswärts" value={ml.b} onChange={(e) => setMl({ ...ml, b: e.target.value })} /></div>
          <div className="flex flex-wrap items-center gap-2"><span className="w-28 text-ink-2">Handicap</span>
            <input className={cell} inputMode="decimal" placeholder={`${game.home} -5.5`} aria-label="Handicap Heim Linie" value={sp.line} onChange={(e) => setSp({ ...sp, line: e.target.value })} />
            <input className={cell} inputMode="decimal" placeholder={game.home} aria-label="Handicap Heim Quote" value={sp.a} onChange={(e) => setSp({ ...sp, a: e.target.value })} />
            <input className={cell} inputMode="decimal" placeholder={game.away} aria-label="Handicap Auswärts Quote" value={sp.b} onChange={(e) => setSp({ ...sp, b: e.target.value })} />
            <span className="text-xs text-ink-3">Linie aus Sicht des Heimteams</span></div>
          <div className="flex flex-wrap items-center gap-2"><span className="w-28 text-ink-2">Over/Under</span>
            <input className={cell} inputMode="decimal" placeholder="228.5" aria-label="Total Linie" value={to.line} onChange={(e) => setTo({ ...to, line: e.target.value })} />
            <input className={cell} inputMode="decimal" placeholder="Over" aria-label="Total Over" value={to.a} onChange={(e) => setTo({ ...to, a: e.target.value })} />
            <input className={cell} inputMode="decimal" placeholder="Under" aria-label="Total Under" value={to.b} onChange={(e) => setTo({ ...to, b: e.target.value })} /></div>
        </div>
        {players.length > 0 && (
          <div className="grid gap-2 border-t border-line pt-3">
            <div className="text-xs uppercase tracking-wide text-ink-3">Spieler-Wetten</div>
            {props.map((p, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2">
                <select value={p.player_id} onChange={(e) => setProp(i, { player_id: e.target.value })} aria-label="Spieler">{players.map((x) => <option key={x.player_id} value={x.player_id}>{x.name} ({x.team})</option>)}</select>
                <select value={p.market} onChange={(e) => setProp(i, { market: e.target.value })} aria-label="Spieler-Markt">{PM.map((m) => <option key={m} value={m}>{MARKET[m]}</option>)}</select>
                <input className={cell} inputMode="decimal" placeholder="Linie" aria-label="Prop Linie" value={p.line} onChange={(e) => setProp(i, { line: e.target.value })} />
                <input className={cell} inputMode="decimal" placeholder="Over" aria-label="Prop Over" value={p.a} onChange={(e) => setProp(i, { a: e.target.value })} />
                <input className={cell} inputMode="decimal" placeholder="Under" aria-label="Prop Under" value={p.b} onChange={(e) => setProp(i, { b: e.target.value })} />
                {props.length > 1 && <Button onClick={() => setProps(props.filter((_, j) => j !== i))} title="Zeile entfernen">✕</Button>}
              </div>
            ))}
            <div><Button onClick={() => setProps([...props, { player_id: players[0]?.player_id ?? "", market: "points", line: "", a: "", b: "" }])}>+ weitere Spieler-Wette</Button></div>
          </div>
        )}
      </div>
      {msg && <div className="mt-3"><Notice tone={msg.ok ? "good" : "bad"}>{msg.text}</Notice></div>}
      {stored.data?.quotes.length > 0 && (
        <div className="mt-4 border-t border-line pt-3 text-xs text-ink-2">
          <div className="mb-1 uppercase tracking-wide text-ink-3">Aktuell gespeicherte Quoten</div>
          <div className="flex flex-wrap gap-x-5 gap-y-1">
            {stored.data.quotes.map((q: any, i: number) => (
              <span key={i} className="whitespace-nowrap">{q.bookmaker}: {q.player_name ? `${q.player_name} ` : ""}{MARKET[q.market]} {q.selection === "home" ? game.home : q.selection === "away" ? game.away : SEL[q.selection]} {q.line ?? ""} <b className="num text-ink">{q.price.toFixed(2)}</b> {q.stale && <Badge tone="warn">veraltet</Badge>}</span>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-ink-3">zuletzt erfasst {time(stored.data.quotes[0].fetched_at)}
            {Array.from(new Set<string>(stored.data.quotes.filter((q: any) => q.source === "manual").map((q: any) => q.bookmaker))).map((b) => (
              <Button key={b} onClick={async () => { await api(`odds/manual?game_id=${gameId}&bookmaker=${encodeURIComponent(b)}`, { method: "DELETE" }); stored.reload(); onSaved?.(); }}>Quoten von {b} löschen</Button>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
