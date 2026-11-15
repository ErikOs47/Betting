"use client";
import { useMemo, useState } from "react";
import { Meter } from "@/components/charts";
import { Badge, Button, Card, DesktopOnly, MobileList, Notice, PageTitle, Scroll, State } from "@/components/ui";
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
  const fair = useApi<any>("fair-lines", 120000);
  const [fm, setFm] = useState("points");
  const [fg, setFg] = useState("");
  const fairGames: [string, string][] = Array.from(new Map<string, string>((fair.data?.lines ?? []).map((l: any) => [l.game_id, l.matchup])).entries());
  const fairGame = fg || fairGames[0]?.[0] || "";
  const fairRows = (fair.data?.lines ?? []).filter((l: any) => l.market === fm && l.game_id === fairGame);
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
      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        <label className="flex items-center gap-2">Markt
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="">alle</option>{Object.keys(MARKET).filter((m) => m !== "parlay").map((m) => <option key={m} value={m}>{MARKET[m]}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">Sortierung
          <select value={sort} onChange={(e) => setSort(e.target.value as any)}><option value="ev">EV</option><option value="edge">Edge</option><option value="confidence">Confidence</option></select>
        </label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> auch abgelehnte Kandidaten zeigen</label>
        {f && <span className="hidden text-xs text-ink-3 sm:inline">Filter: Edge ≥ {pct(f.min_edge, 0)} · Confidence ≥ {f.min_confidence} · EV &gt; {pct(f.min_ev, 0)} · Datenqualität ≥ {pct(f.min_data_quality, 0)}</span>}
      </div>
      <Card title={`${data?.count_passing ?? 0} Value Bets erfüllen alle Filter`}>
        <State loading={loading} error={error} empty={rows.length === 0 && "Keine Kandidaten. Erfasse Buchmacherquoten (z. B. Tipico) in der Spielanalyse eines Spiels – sie erscheinen dann hier. Unten stehen die fairen Linien des Modells mit Mindestquoten."}>
          <MobileList>
            {rows.map((c: any, i: number) => (
              <div key={i} className={`px-3 py-3 ${c.passes ? "" : "opacity-60"}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{c.player_name ?? MARKET[c.market]}</div>
                    <div className="truncate text-xs text-ink-2">{c.player_name ? `${MARKET[c.market]} ` : ""}{SEL[c.selection]} {c.line ?? ""} · {c.matchup} · {time(c.tip_utc)}</div>
                  </div>
                  <div className="num shrink-0 text-right"><div className="font-semibold">{c.price.toFixed(2)}</div><div className="text-xs text-ink-3">{c.bookmaker}</div></div>
                </div>
                <div className="mt-2 grid grid-cols-4 gap-2 text-xs">
                  <div><div className="text-ink-3">Modell</div><div className="num">{pct(c.model_prob)}</div></div>
                  <div><div className="text-ink-3">Edge</div><div className="num">{spct(c.edge)}</div></div>
                  <div><div className="text-ink-3">EV</div><div className="num">{spct(c.ev)}</div></div>
                  <div><div className="text-ink-3">Conf.</div><div className="num">{c.confidence}</div></div>
                </div>
                {!c.passes && <div className="mt-1.5 flex flex-wrap gap-1">{c.fail_reasons.map((r: string) => <Badge key={r} tone="warn">{FAIL[r] ?? r}</Badge>)}</div>}
                <div className="mt-2 flex items-center justify-between gap-2">
                  <span className="num text-xs text-ink-2">Einsatz {c.stake.blocked ? "–" : money(c.stake.amount)} · fair {c.fair_odds.toFixed(2)}</span>
                  <div className="flex gap-1.5">
                    <Button onClick={() => slip.add({ ...leg(c), label: `${c.player_name ?? c.matchup} ${MARKET[c.market]} ${SEL[c.selection]} ${c.line ?? ""}` })}>+ Kombi</Button>
                    <Button kind="primary" disabled={!c.passes || c.stake.blocked || data.kill_switch} onClick={() => place(c)}>Wetten</Button>
                  </div>
                </div>
              </div>
            ))}
          </MobileList>
          <DesktopOnly><Scroll>
            <table>
              <thead><tr><th>Partie</th><th>Wette</th><th>Beste Quote</th><th>Faire Quote</th><th>Modell</th><th>Markt (fair)</th><th>Edge</th><th>EV</th><th>Confidence</th><th>Einsatz (¼ Kelly)</th><th></th></tr></thead>
              <tbody>
                {rows.map((c: any, i: number) => (
                  <>
                    <tr key={i} className={c.passes ? "" : "opacity-60"}>
                      <td><div>{c.matchup}</div><div className="text-xs text-ink-3">{time(c.tip_utc)}</div></td>
                      <td><div className="font-medium">{c.player_name ?? MARKET[c.market]}</div><div className="text-xs text-ink-2">{c.player_name ? `${MARKET[c.market]} ` : ""}{SEL[c.selection]} {c.line ?? ""}</div></td>
                      <td className="num"><b>{c.price.toFixed(2)}</b><div className="text-xs text-ink-3">{c.bookmaker} · {c.n_books} {c.n_books === 1 ? "Buch" : "Bücher"}{c.price_net < c.price - 1e-9 ? ` · netto ${c.price_net.toFixed(2)}` : ""}</div></td>
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
          </Scroll></DesktopOnly>
        </State>
      </Card>
      <div className="mt-5">
        <Card title="Faire Linien des Modells" right={<div className="flex gap-2">
          <select value={fairGame} onChange={(e) => setFg(e.target.value)} aria-label="Partie faire Linien">{fairGames.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>
          <select value={fm} onChange={(e) => setFm(e.target.value)} aria-label="Markt faire Linien">
            {Object.keys(MARKET).filter((m) => m !== "parlay").map((m) => <option key={m} value={m}>{MARKET[m]}</option>)}
          </select></div>}>
          <p className="mb-3 text-xs text-ink-3"><b className="text-ink-2">Ohne Quoten-Feed nutzbar.</b> Vergleiche diese Werte mit der Quote deines Buchmachers. „Mindestquote“ ist die Quote, ab der der Edge-Filter ({pct(fair.data?.min_edge, 0)}) sicher erfüllt ist{fair.data?.payout_tax > 0 ? `, inklusive ${pct(fair.data.payout_tax, 1)} Wettsteuer` : ""} – gültig nur für genau die angegebene Linie. Bietet der Buchmacher eine andere Linie, nutze „Quote prüfen“ in der Spielanalyse.</p>
          <State loading={fair.loading} error={fair.error} empty={fairRows.length === 0 && "Keine aktiven Prognosen für anstehende Spiele in diesem Markt."}>
            <MobileList>
              {fairRows.map((l: any, i: number) => {
                const two = fm === "moneyline" || fm === "spread";
                return (
                  <div key={i} className="flex items-center justify-between gap-3 px-3 py-2.5">
                    <div className="min-w-0">
                      <div className="truncate font-medium">{l.player_name ?? MARKET[l.market]} {l.team && <span className="font-normal text-ink-3">{l.team}</span>}</div>
                      <div className="num text-xs text-ink-2">{l.line == null ? `Erwartete Margin ${l.mean > 0 ? "+" : ""}${l.mean}` : fm === "spread" ? `Heim ${l.line > 0 ? "+" : ""}${l.line}` : `Linie ${l.line} · Ø ${l.mean}`} · {two ? "Heim" : "Over"} {pct(l.p_a, 0)}</div>
                    </div>
                    <div className="num shrink-0 text-right text-xs">
                      <div><span className="text-ink-3">{two ? "H" : "O"} ab </span><b className="text-sm text-ink">{l.min_a?.toFixed(2) ?? "–"}</b></div>
                      <div><span className="text-ink-3">{two ? "A" : "U"} ab </span><b className="text-sm text-ink">{l.min_b?.toFixed(2) ?? "–"}</b></div>
                    </div>
                  </div>
                );
              })}
            </MobileList>
            <DesktopOnly><Scroll>
              <table>
                <thead><tr><th>Partie</th><th>{fm === "moneyline" || fm === "spread" || fm === "total" ? "Markt" : "Spieler"}</th><th>Erwartung</th><th>Linie</th><th>Wahrsch. {fm === "moneyline" || fm === "spread" ? "Heim" : "Over"}</th><th>Faire Quote {fm === "moneyline" || fm === "spread" ? "Heim / Auswärts" : "Over / Under"}</th><th>Mindestquote {fm === "moneyline" || fm === "spread" ? "Heim / Auswärts" : "Over / Under"}</th><th>Datenqualität</th></tr></thead>
                <tbody>
                  {fairRows.map((l: any, i: number) => (
                    <tr key={i}>
                      <td><div>{l.matchup}</div><div className="text-xs text-ink-3">{time(l.tip_utc)}</div></td>
                      <td className="font-medium">{l.player_name ? <>{l.player_name} <span className="font-normal text-ink-3">{l.team}</span></> : MARKET[l.market]}</td>
                      <td className="num">{l.mean > 0 && (fm === "moneyline" || fm === "spread") ? "+" : ""}{l.mean}</td>
                      <td className="num">{l.line == null ? "–" : fm === "spread" ? `Heim ${l.line > 0 ? "+" : ""}${l.line}` : l.line}</td>
                      <td className="num">{pct(l.p_a)}</td>
                      <td className="num">{l.fair_a.toFixed(2)} / {l.fair_b.toFixed(2)}</td>
                      <td className="num font-medium">{l.min_a?.toFixed(2) ?? "–"} / {l.min_b?.toFixed(2) ?? "–"}</td>
                      <td className="num">{pct(l.data_quality, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scroll></DesktopOnly>
          </State>
        </Card>
      </div>
    </>
  );
}
const PART: Record<string, string> = { data_quality: "Datenqualität (35 %)", calibration: "Kalibrierung im Backtest (20 %)", agreement: "Modell-Übereinstimmung (20 %)", market_sanity: "Markt-Plausibilität (15 %)", depth: "Anzahl Buchmacher (10 %)" };
