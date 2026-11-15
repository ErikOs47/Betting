"use client";
import { useMemo, useState } from "react";
import { Meter } from "@/components/charts";
import { Badge, Button, Card, Notice, PageTitle, Scroll, State } from "@/components/ui";
import { FLAG, MARKET, SEL, api, money, pct, spct, time, useApi, useSlip } from "@/lib/api";

const FAIL: Record<string, string> = {
  "prediction disabled (data quality)": "Prognose deaktiviert", "market disabled": "Markt deaktiviert", "edge below minimum": "Edge zu klein",
  "edge implausibly large": "Edge unplausibel groß", "EV not positive": "EV nicht positiv", "confidence below minimum": "Confidence zu niedrig",
  "data quality below minimum": "Datenqualität zu niedrig", "price outside allowed range": "Quote außerhalb Bereich", "odds stale": "Quote veraltet",
};

export default function ValueFinder() {
  const [all, setAll] = useState(false);
  const [market, setMarket] = useState("");
  const [sort, setSort] = useState<"ev" | "edge" | "confidence">("ev");
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, loading, reload } = useApi<any>(`value-bets?all=${all}`, 60000);
  const slip = useSlip();
  const rows = useMemo(() => (data?.candidates ?? []).filter((c: any) => !market || c.market === market)
    .sort((a: any, b: any) => Number(b.passes) - Number(a.passes) || b[sort] - a[sort]), [data, market, sort]);
  const leg = (c: any) => ({ game_id: c.game_id, market: c.market, selection: c.selection, player_id: c.player_id, line: c.line, price: c.price, bookmaker: c.bookmaker });
  const place = async (c: any) => {
    try {
      const b = await api("bets", { method: "POST", body: { legs: [leg(c)] } });
      setMsg({ tone: "good", text: `Paper-Wette #${b.id} platziert: ${b.description} · Einsatz ${money(b.stake)} @ ${b.price.toFixed(2)}` });
      reload();
    } catch (e) { setMsg({ tone: "bad", text: (e as Error).message }); }
  };
  const f = data?.filters;
  return (
    <>
      <PageTitle title="Value Finder" sub="Vergleich der Modellwahrscheinlichkeit mit margenbereinigten Buchmacherquoten. EV = Modellwahrscheinlichkeit × Quote − 1; Edge = Modell − marktfaire Wahrscheinlichkeit (Konsens aller Buchmacher)." />
      {msg && <div className="mb-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2">Markt
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="">alle</option>{Object.keys(MARKET).filter((m) => m !== "parlay").map((m) => <option key={m} value={m}>{MARKET[m]}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">Sortierung
          <select value={sort} onChange={(e) => setSort(e.target.value as any)}><option value="ev">EV</option><option value="edge">Edge</option><option value="confidence">Confidence</option></select>
        </label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> auch abgelehnte Kandidaten zeigen</label>
        {f && <span className="text-xs text-ink-3">Filter: Edge ≥ {pct(f.min_edge, 0)} · Confidence ≥ {f.min_confidence} · EV &gt; {pct(f.min_ev, 0)} · Datenqualität ≥ {pct(f.min_data_quality, 0)}</span>}
      </div>
      <Card title={`${data?.count_passing ?? 0} Value Bets erfüllen alle Filter`}>
        <State loading={loading} error={error} empty={rows.length === 0 && "Keine Kandidaten. Ohne aktuelle Quoten (Odds-API-Schlüssel) kann der Value Finder nichts vergleichen – nutze dann die manuelle Quoteneingabe in der Spielanalyse."}>
          <Scroll>
            <table>
              <thead><tr><th>Partie</th><th>Wette</th><th>Beste Quote</th><th>Faire Quote</th><th>Modell</th><th>Markt (fair)</th><th>Edge</th><th>EV</th><th>Confidence</th><th>Einsatz (¼ Kelly)</th><th></th></tr></thead>
              <tbody>
                {rows.map((c: any, i: number) => (
                  <>
                    <tr key={i} className={c.passes ? "" : "opacity-60"}>
                      <td><div>{c.matchup}</div><div className="text-xs text-ink-3">{time(c.tip_utc)}</div></td>
                      <td><div className="font-medium">{c.player_name ?? MARKET[c.market]}</div><div className="text-xs text-ink-2">{c.player_name ? `${MARKET[c.market]} ` : ""}{SEL[c.selection]} {c.line ?? ""}</div></td>
                      <td className="num"><b>{c.price.toFixed(2)}</b><div className="text-xs text-ink-3">{c.bookmaker} · {c.n_books} Bücher</div></td>
                      <td className="num">{c.fair_odds.toFixed(2)}</td>
                      <td className="num">{pct(c.model_prob)}</td>
                      <td className="num">{pct(c.consensus_prob)}<div className="text-xs text-ink-3">Marge {pct(c.overround)}</div></td>
                      <td className="num">{spct(c.edge)}</td><td className="num">{spct(c.ev)}</td>
                      <td><Meter value={c.confidence / 100} /></td>
                      <td className="num">{c.stake.blocked ? <span className="text-ink-3" title={c.stake.reasons.join("; ")}>–</span> : <>{money(c.stake.amount)}<div className="text-xs text-ink-3">{pct(c.stake.pct_bankroll, 2)}</div></>}</td>
                      <td className="whitespace-nowrap">
                        <div className="flex gap-1.5">
                          <Button onClick={() => setOpen(open === i ? null : i)}>Details</Button>
                          <Button onClick={() => slip.add({ ...leg(c), label: `${c.player_name ?? c.matchup} ${MARKET[c.market]} ${SEL[c.selection]} ${c.line ?? ""}` })}>+ Slip</Button>
                          <Button kind="primary" disabled={!c.passes || c.stake.blocked || data.kill_switch} onClick={() => place(c)}>Paper-Wette</Button>
                        </div>
                      </td>
                    </tr>
                    {open === i && (
                      <tr key={`${i}-d`}><td colSpan={11} className="bg-surface-2">
                        <div className="grid gap-4 text-xs md:grid-cols-3">
                          <div><div className="mb-1 font-medium text-ink">Confidence-Komponenten (0–1)</div>
                            {Object.entries(c.confidence_parts).map(([k, v]) => <div key={k} className="flex justify-between"><span className="text-ink-2">{PART[k]}</span><span className="num">{(v as number).toFixed(2)}</span></div>)}</div>
                          <div><div className="mb-1 font-medium text-ink">Wahrscheinlichkeiten</div>
                            <div className="flex justify-between"><span className="text-ink-2">LightGBM</span><span className="num">{pct(c.p_primary)}</span></div>
                            <div className="flex justify-between"><span className="text-ink-2">XGBoost</span><span className="num">{pct(c.p_alt)}</span></div>
                            <div className="flex justify-between"><span className="text-ink-2">Implizit (mit Marge)</span><span className="num">{pct(c.implied_prob)}</span></div>
                            <div className="flex justify-between"><span className="text-ink-2">Push</span><span className="num">{pct(c.p_push)}</span></div>
                            <div className="flex justify-between"><span className="text-ink-2">Voll-Kelly</span><span className="num">{pct(c.kelly_full)}</span></div></div>
                          <div><div className="mb-1 font-medium text-ink">Prüfungen</div>
                            {c.fail_reasons.length ? c.fail_reasons.map((r: string) => <div key={r}><Badge tone="warn">{FAIL[r] ?? r}</Badge></div>) : <Badge tone="good">alle Filter erfüllt</Badge>}
                            <div className="mt-2 flex flex-wrap gap-1">{c.quality_flags.map((q: string) => <Badge key={q}>{FLAG[q] ?? q}</Badge>)}</div>
                            {c.other_books.length > 0 && <div className="mt-2 text-ink-2">Weitere Quoten: {c.other_books.map((o: any) => `${o.bookmaker} ${o.price.toFixed(2)}`).join(" · ")}</div>}
                            <div className="mt-1 text-ink-3">Prognose-ID {c.prediction_id} · Quote vom {time(c.fetched_at)}</div></div>
                        </div>
                      </td></tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </Scroll>
        </State>
      </Card>
    </>
  );
}
const PART: Record<string, string> = { data_quality: "Datenqualität (35 %)", calibration: "Kalibrierung im Backtest (20 %)", agreement: "Modell-Übereinstimmung (20 %)", market_sanity: "Markt-Plausibilität (15 %)", depth: "Anzahl Buchmacher (10 %)" };
