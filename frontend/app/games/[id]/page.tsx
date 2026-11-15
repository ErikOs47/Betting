"use client";
import Link from "next/link";
import { use, useState } from "react";
import { DistChart } from "@/components/charts";
import { OddsEntry } from "@/components/odds-entry";
import { Badge, Button, Card, DesktopOnly, MobileList, Notice, PageTitle, Scroll, State } from "@/components/ui";
import { FLAG, MARKET, SEL, api, money, pct, spct, time, useApi, useSlip } from "@/lib/api";

const PM = ["points", "rebounds", "assists", "threes", "pra"] as const;
const ST: Record<string, "bad" | "warn" | "good" | "info"> = { Out: "bad", Doubtful: "bad", Questionable: "warn", Probable: "info", Available: "good" };

function Form({ f }: { f: any }) {
  if (!f.n) return <div className="text-sm text-ink-3">Keine Spiele geladen.</div>;
  return (
    <div className="text-sm">
      <div className="mb-2 font-medium">{f.name} <span className="text-ink-3">· letzte {f.n}: {f.record}</span></div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        {[["Off. Rating", f.off_rtg], ["Def. Rating", f.def_rtg], ["Net Rating", f.net_rtg], ["Pace", f.pace], ["Punkte", f.pts], ["Ruhetage", f.rest_days]].map(([k, v]) => (
          <div key={k as string} className="rounded-md bg-surface-2 px-2 py-1.5"><div className="text-ink-3">{k}</div><div className="num text-sm text-ink">{v}</div></div>
        ))}
      </div>
      {f.back_to_back && <div className="mt-2"><Badge tone="warn">Back-to-back</Badge></div>}
      <div className="mt-2 text-xs text-ink-3">{f.last_games.map((g: any) => `${g.home ? "vs" : "@"} ${g.opp} ${g.pts}:${g.opp_pts}`).join(" · ")}</div>
    </div>
  );
}

function Evaluator({ gameId, players, game }: { gameId: string; players: any[]; game: any }) {
  const slip = useSlip();
  const [market, setMarket] = useState("points");
  const [pid, setPid] = useState(players[0]?.player_id ?? "");
  const [sel, setSel] = useState("over");
  const [line, setLine] = useState("");
  const [price, setPrice] = useState("");
  const [opp, setOpp] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const isPlayer = (PM as readonly string[]).includes(market);
  const sels = isPlayer || market === "total" ? ["over", "under"] : ["home", "away"];
  const { data: pd } = useApi<any>(isPlayer && pid ? `players/${pid}?game_id=${gameId}` : null);
  const leg = () => ({ game_id: gameId, market, selection: sels.includes(sel) ? sel : sels[0], player_id: isPlayer ? pid : null,
    line: market === "moneyline" ? null : Number(line), price: Number(price), opposite_price: opp ? Number(opp) : null });
  const run = async () => {
    setErr(null); setRes(null);
    try { setRes(await api("evaluate", { method: "POST", body: leg() })); } catch (e) { setErr((e as Error).message); }
  };
  const d = pd?.distributions?.[market];
  const name = players.find((p) => p.player_id === pid)?.name;
  return (
    <Card title="Quote prüfen (manuelle Eingabe)">
      <p className="mb-3 text-xs text-ink-3">Linie und Dezimalquote eines Buchmachers eingeben. Mit der Gegenquote wird die Marge entfernt und der Edge gegen die faire Marktwahrscheinlichkeit berechnet.</p>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="grid gap-1">Markt<select data-testid="eval-market" value={market} onChange={(e) => { setMarket(e.target.value); setRes(null); }}>{Object.keys(MARKET).filter((m) => m !== "parlay").map((m) => <option key={m} value={m}>{MARKET[m]}</option>)}</select></label>
        {isPlayer && <label className="grid gap-1">Spieler<select value={pid} onChange={(e) => { setPid(e.target.value); setRes(null); }}>{players.map((p) => <option key={p.player_id} value={p.player_id}>{p.name} ({p.team})</option>)}</select></label>}
        <label className="grid gap-1">Auswahl<select value={sels.includes(sel) ? sel : sels[0]} onChange={(e) => setSel(e.target.value)}>{sels.map((s) => <option key={s} value={s}>{s === "home" ? game.home : s === "away" ? game.away : SEL[s]}</option>)}</select></label>
        {market !== "moneyline" && <label className="grid gap-1">Linie<input className="w-24" inputMode="decimal" value={line} onChange={(e) => setLine(e.target.value)} placeholder={market === "spread" ? "-5.5" : "24.5"} /></label>}
        <label className="grid gap-1">Quote<input className="w-24" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="1.91" /></label>
        <label className="grid gap-1">Gegenquote (optional)<input className="w-24" inputMode="decimal" value={opp} onChange={(e) => setOpp(e.target.value)} placeholder="1.91" /></label>
        <Button kind="primary" onClick={run} disabled={!price || (market !== "moneyline" && line === "")}>Berechnen</Button>
      </div>
      {err && <div className="mt-3"><Notice tone="bad">{err}</Notice></div>}
      {res && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-6">
            {[["Modell-Wahrsch.", pct(res.model_prob)], ["Faire Quote", res.fair_odds.toFixed(2)], ["Markt fair", pct(res.book_prob)], ["Edge", spct(res.edge)], ["EV", spct(res.ev)],
              ["Einsatz (¼ Kelly)", res.stake.blocked ? "–" : money(res.stake.amount)]].map(([k, v]) => (
              <div key={k} className="rounded-md bg-surface-2 px-3 py-2"><div className="text-xs text-ink-3">{k}</div><div className="num font-semibold">{v}</div></div>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {res.ev > 0 ? <Badge tone="good">positiver Erwartungswert</Badge> : <Badge tone="warn">kein positiver Erwartungswert</Badge>}
            <Badge tone={res.enabled ? "good" : "bad"}>Datenqualität {pct(res.data_quality, 0)}</Badge>
            {res.quality_flags.map((f: string) => <Badge key={f}>{FLAG[f] ?? f}</Badge>)}
            {res.stake.reasons.map((r: string) => <span key={r} className="text-ink-3">{r}</span>)}
            <Button onClick={() => slip.add({ ...leg(), bookmaker: "manual", label: `${isPlayer ? name : `${game.away} @ ${game.home}`} ${MARKET[market]} ${SEL[leg().selection]} ${market === "moneyline" ? "" : line}` })}>+ Bet Slip</Button>
          </div>
        </div>
      )}
      {isPlayer && d && (
        <div className="mt-4">
          <div className="mb-1 text-sm font-medium">{name}: Verteilung {MARKET[market]} <span className="font-normal text-ink-3">· Erwartung {d.mean.toFixed(1)} · 5–95 %: {d.q["5"]}–{d.q["95"]} · XGBoost {d.alt_mean?.toFixed(1) ?? "–"}</span></div>
          <DistChart pmf={d.pmf} mean={d.mean} line={line === "" ? null : Number(line)} side={sel as any} ariaLabel={`Wahrscheinlichkeitsverteilung ${MARKET[market]} ${name}`} />
          <div className="mt-2 text-xs text-ink-3">Letzte Spiele: {pd.log.slice(0, 10).map((g: any) => g[market === "points" ? "pts" : market === "rebounds" ? "trb" : market === "assists" ? "ast" : market === "threes" ? "fg3" : "pra"]).join(" · ")} · Prognose-ID {d.prediction_id} ({d.inputs_hash.slice(0, 8)})</div>
        </div>
      )}
    </Card>
  );
}

export default function GameDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, loading } = useApi<any>(`games/${id}`);
  const g = data?.game;
  return (
    <State loading={loading} error={error}>
      {data && (
        <div className="space-y-5">
          <PageTitle title={`${g.away_name} @ ${g.home_name}`} sub={<>{time(g.tip_utc)} · {g.status === "final" ? `Endstand ${g.away_pts}:${g.home_pts}` : "noch nicht gespielt"} · <Link className="text-s1 underline" href="/games">zurück</Link></>} />
          {g.model && (
            <div className="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
              {[["Heimsieg-Wahrscheinlichkeit", pct(g.model.home_win_prob), `faire Quote ${g.model.fair_home.toFixed(2)} / ${g.model.fair_away.toFixed(2)}`],
                ["Erwartete Margin (Heim)", `${g.model.margin > 0 ? "+" : ""}${g.model.margin}`, `Standardabw. ${g.model.sigma_margin.toFixed(1)}`],
                ["Erwartete Gesamtpunkte", g.model.total, `Standardabw. ${g.model.sigma_total.toFixed(1)}`],
                ["Datenqualität", pct(g.model.data_quality, 0), g.model.enabled ? "Prognose aktiv" : "Prognose deaktiviert"]].map(([k, v, s]) => (
                <div key={k as string} className="min-w-0 rounded-xl border border-line bg-surface p-3 sm:p-4"><div className="text-[0.68rem] uppercase tracking-wide text-ink-3 sm:text-xs">{k}</div><div className="num mt-1 text-xl font-semibold sm:text-2xl">{v}</div><div className="mt-1 text-xs text-ink-3">{s}</div></div>
              ))}
            </div>
          )}
          {!g.model && g.status !== "final" && <Notice tone="warn">Für dieses Spiel liegt keine Prognose vor (Modelle trainieren und „Prognosen“ in den Einstellungen aktualisieren).</Notice>}
          <div className="grid gap-5 xl:grid-cols-3">
            <Card title="Form & Matchup (letzte 10 Spiele)" className="xl:col-span-2">
              <div className="grid gap-5 md:grid-cols-2"><Form f={data.form.away} /><Form f={data.form.home} /></div>
              {data.model_inputs && <div className="mt-4 text-xs text-ink-3">Modell-Eingaben: Elo-Differenz inkl. Heimvorteil {data.model_inputs.elo_diff} · fehlende Minuten Heim {data.model_inputs.h_miss_min} / Auswärts {data.model_inputs.a_miss_min} · erwartete Pace {data.model_inputs.x_pace_l10}</div>}
              {data.head_to_head.length > 0 && <div className="mt-3 text-xs text-ink-2">Direkte Duelle: {data.head_to_head.map((h: any) => `${h.date.slice(2)} ${h.away} ${h.away_pts}:${h.home_pts} ${h.home}`).join(" · ")}</div>}
            </Card>
            <Card title="Verletzungen (offizieller NBA Injury Report)" right={<span className="text-xs text-ink-3">{time(data.injury_report_utc)}</span>}>
              <State empty={data.injuries.length === 0 && "Keine Einträge für dieses Spiel im aktuellen Report."}>
                <div className="max-h-72 space-y-1.5 overflow-y-auto text-sm">
                  {data.injuries.map((i: any, k: number) => (
                    <div key={k} className="flex items-center justify-between gap-2"><span><span className="text-ink-3">{i.team}</span> {i.player}<span className="block text-xs text-ink-3">{i.reason}</span></span><Badge tone={ST[i.status] ?? "info"}>{i.status}</Badge></div>
                  ))}
                </div>
              </State>
            </Card>
          </div>
          {g.status !== "final" && g.model && <OddsEntry gameId={id} game={g} players={data.players} />}
          {data.players.length > 0 && <Evaluator gameId={id} players={data.players} game={g} />}
          {data.players.length > 0 && (
            <Card title="Spielerprognosen (Erwartungswert · 10–90 %-Bereich)">
              <MobileList>
                {data.players.map((p: any) => (
                  <div key={p.player_id} className={`px-3 py-2.5 ${p.enabled ? "" : "opacity-55"}`}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0 truncate font-medium">{p.name} <span className="font-normal text-ink-3">{p.team} · {p.min_l10?.toFixed(0) ?? "–"} Min</span></div>
                      <Badge tone={p.enabled ? "good" : "warn"}>{pct(p.data_quality, 0)}</Badge>
                    </div>
                    <div className="mt-1 grid grid-cols-5 gap-1 text-center text-xs">
                      {PM.map((m) => <div key={m}><div className="text-ink-3">{m === "points" ? "PTS" : m === "rebounds" ? "REB" : m === "assists" ? "AST" : m === "threes" ? "3P" : "PRA"}</div><div className="num text-sm text-ink">{p.proj[m]?.mean.toFixed(1) ?? "–"}</div><div className="num text-[0.65rem] text-ink-3">{p.proj[m] ? `${p.proj[m].p10}–${p.proj[m].p90}` : ""}</div></div>)}
                    </div>
                  </div>
                ))}
              </MobileList>
              <DesktopOnly><Scroll>
                <table>
                  <thead><tr><th>Spieler</th><th>Team</th><th>Min Ø10</th><th>Usage Ø10</th>{PM.map((m) => <th key={m}>{MARKET[m]}</th>)}<th>Qualität</th></tr></thead>
                  <tbody>
                    {data.players.map((p: any) => (
                      <tr key={p.player_id} className={p.enabled ? "" : "opacity-55"}>
                        <td className="font-medium">{p.name}</td><td className="text-ink-2">{p.team}</td>
                        <td className="num">{p.min_l10?.toFixed(1) ?? "–"}</td><td className="num">{p.usg_l10?.toFixed(1) ?? "–"}</td>
                        {PM.map((m) => <td key={m} className="num whitespace-nowrap">{p.proj[m] ? <>{p.proj[m].mean.toFixed(1)} <span className="text-xs text-ink-3">{p.proj[m].p10}–{p.proj[m].p90}</span></> : "–"}</td>)}
                        <td><div className="flex flex-wrap gap-1"><Badge tone={p.enabled ? "good" : "warn"}>{pct(p.data_quality, 0)}</Badge>{p.flags.filter((f: string) => f !== "injury_report_stale").map((f: string) => <Badge key={f}>{FLAG[f] ?? f}</Badge>)}</div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Scroll></DesktopOnly>
            </Card>
          )}
          {data.box.length > 0 && (
            <Card title="Boxscore">
              <Scroll><table><thead><tr><th>Spieler</th><th>Team</th><th>Min</th><th>PTS</th><th>REB</th><th>AST</th><th>3P</th></tr></thead>
                <tbody>{data.box.map((b: any) => <tr key={b.player_id}><td>{b.name}{b.starter && <span className="ml-1 text-xs text-ink-3">S</span>}</td><td className="text-ink-2">{b.team}</td><td className="num">{b.min}</td><td className="num">{b.pts}</td><td className="num">{b.trb}</td><td className="num">{b.ast}</td><td className="num">{b.fg3}</td></tr>)}</tbody></table></Scroll>
            </Card>
          )}
        </div>
      )}
    </State>
  );
}
