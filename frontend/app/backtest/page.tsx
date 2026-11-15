"use client";
import { useEffect, useState } from "react";
import { CalibrationChart, LineChart, SERIES } from "@/components/charts";
import { Card, Notice, PageTitle, Scroll, Stat, State } from "@/components/ui";
import { MARKET, money, pct, spct, time, useApi } from "@/lib/api";

const PM = ["points", "rebounds", "assists", "threes", "pra"];
const ALGO: Record<string, string> = { lightgbm: "LightGBM", xgboost: "XGBoost", ensemble: "Ensemble", elo_baseline: "Elo-Baseline", home_rate_baseline: "Heimquote-Baseline" };

export default function Backtest() {
  const runs = useApi<any>("backtests");
  const [id, setId] = useState<number | null>(null);
  const [market, setMarket] = useState("points");
  useEffect(() => { if (id == null && runs.data?.runs.length) setId(runs.data.runs[0].id); }, [runs.data, id]);
  const run = useApi<any>(id == null ? null : `backtests/${id}`);
  const m = run.data?.metrics;
  const algos: string[] = run.data?.config.algos ?? [];
  const isPlayer = PM.includes(market);
  const calib = m ? (isPlayer
    ? [...algos, "ensemble"].map((a, i) => ({ label: ALGO[a], color: SERIES[i], bins: m.player[a]?.[market]?.event?.bins ?? [] }))
    : [...algos, "ensemble"].filter((a) => m.game[a]).map((a, i) => ({ label: ALGO[a], color: SERIES[i], bins: m.game[a][market]?.bins ?? [] }))) : [];
  const foldSeries = (key: string) => algos.map((a, i) => ({ label: ALGO[a], color: SERIES[i],
    points: (m?.folds ?? []).map((f: any, k: number) => ({ x: k + 1, y: f[`${a}_${key}`] })).filter((p: any) => p.y != null) }));
  return (
    <>
      <PageTitle title="Backtesting" sub="Walk-forward-Validierung: Jedes Modell wird nur mit Spielen vor dem Testblock trainiert; alle Kennzahlen sind out-of-sample."
        right={runs.data?.runs.length > 0 && (
          <select value={id ?? ""} onChange={(e) => setId(Number(e.target.value))} aria-label="Backtest-Lauf">
            {runs.data.runs.map((r: any) => <option key={r.id} value={r.id}>#{r.id} {r.name} · {time(r.created_at)}</option>)}
          </select>
        )} />
      <State loading={runs.loading || run.loading} error={runs.error || run.error} empty={runs.data?.runs.length === 0 && "Noch kein Backtest vorhanden. Starte einen Lauf in den Einstellungen (Job „Backtest“) oder per CLI: python -m app.cli backtest"}>
        {m && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Testzeitraum (out-of-sample)" value={<span className="text-lg whitespace-nowrap">{fmtD(m.period.start)} – {fmtD(m.period.end)}</span>} sub={`Trainingsdaten ab ${fmtD(m.period.train_from)}`} />
              <Stat label="Testfälle" value={m.period.n_player_rows.toLocaleString("de-DE")} sub={`Spieler-Spiele · ${m.period.n_games} Partien`} />
              <Stat label="Folds" value={m.period.n_folds} sub={`Schrittweite ${run.data.config.step_days} Tage, expandierendes Fenster`} />
              <Stat label="Code / Daten" value={run.data.code_sha?.slice(0, 7) || "–"} sub={`Daten-Hash ${m.data_hash}`} />
            </div>

            <Card title="Wett-Simulation mit historischen Quoten (ROI, Yield, CLV, Drawdown)">
              {m.odds.available ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                    <Stat label="Wetten" value={m.odds.summary.n_bets} sub={`Umsatz ${money(m.odds.summary.turnover)}`} />
                    <Stat label="ROI" value={spct(m.odds.summary.roi)} sub={`Yield ${spct(m.odds.summary.yield)}`} />
                    <Stat label="Trefferquote" value={pct(m.odds.summary.hit_rate)} />
                    <Stat label="Ø CLV" value={spct(m.odds.summary.avg_clv)} sub={`positiv ${pct(m.odds.summary.clv_positive_share, 0)}`} />
                    <Stat label="Max. Drawdown" value={pct(m.odds.summary.max_drawdown)} />
                  </div>
                  <LineChart ariaLabel="Bankroll im Backtest" yFmt={(v) => `${v.toFixed(0)} €`} xFmt={(v) => `#${v}`}
                    series={[{ label: "Bankroll", color: SERIES[0], points: m.odds.equity.map((e: any, i: number) => ({ x: i + 1, y: e.equity })) }]} />
                </div>
              ) : (
                <Notice tone="warn">Für die Testspiele sind keine historischen Quoten gespeichert, daher werden keine ROI-, Yield-, CLV- oder Drawdown-Werte ausgewiesen (es werden keine Quoten erfunden). Historische Quoten liefert The Odds API nur in kostenpflichtigen Tarifen: <code>python -m app.cli historical-odds START ENDE</code>. Die Prognosegüte unten ist davon unabhängig und basiert vollständig auf echten Ergebnissen.</Notice>
              )}
            </Card>

            <Card title="Spieler-Props: Prognosegüte">
              <Scroll>
                <table>
                  <thead><tr><th>Markt</th><th>Modell</th><th>MAE</th><th>MAE Ø letzte 10</th><th>MAE Saison-Ø</th><th>Neg. Log-Likelihood</th><th>Abdeckung 50 / 80 / 90 %</th><th>Brier</th><th>Brier Basisrate</th><th>Log Loss</th><th>ECE</th></tr></thead>
                  <tbody>
                    {PM.flatMap((mk) => algos.map((a) => {
                      const r = m.player[a][mk];
                      return (
                        <tr key={mk + a}>
                          <td>{MARKET[mk]}</td><td className="text-ink-2">{ALGO[a]}</td><td className="num font-medium">{r.mae.toFixed(3)}</td>
                          <td className="num">{r.mae_baseline_l10.toFixed(3)}</td><td className="num">{r.mae_baseline_season.toFixed(3)}</td><td className="num">{r.nll.toFixed(3)}</td>
                          <td className="num">{pct(r.coverage["0.5"], 0)} / {pct(r.coverage["0.8"], 0)} / {pct(r.coverage["0.9"], 0)}</td>
                          <td className="num">{r.event.brier.toFixed(4)}</td><td className="num">{r.event.brier_base_rate.toFixed(4)}</td><td className="num">{r.event.log_loss.toFixed(4)}</td><td className="num whitespace-nowrap">{pct(r.event.ece, 2)}</td>
                        </tr>
                      );
                    }))}
                  </tbody>
                </table>
              </Scroll>
              <p className="mt-3 text-xs text-ink-3">Brier, Log Loss und ECE beziehen sich auf das Ereignis „Spieler übertrifft seine eigene Ø-Leistung der letzten 10 Spiele (abgerundet + 0,5)“ – eine Kalibrierungsschwelle, keine Buchmacherlinie. Nur Rotationsspieler (≥ 20 Min., ≥ 10 Spiele).</p>
            </Card>

            {Object.keys(m.game).length > 0 && (
              <Card title="Spielmärkte: Prognosegüte">
                <Scroll>
                  <table>
                    <thead><tr><th>Modell</th><th>Moneyline Brier</th><th>Log Loss</th><th>Trefferquote</th><th>ECE</th><th>Margin MAE</th><th>Margin MAE Elo</th><th>Total MAE</th><th>Total MAE Baseline</th></tr></thead>
                    <tbody>
                      {Object.entries<any>(m.game).map(([a, r]) => (
                        <tr key={a}>
                          <td>{ALGO[a] ?? a}</td><td className="num font-medium">{r.moneyline?.brier?.toFixed(4) ?? "–"}</td><td className="num">{r.moneyline?.log_loss?.toFixed(4) ?? "–"}</td>
                          <td className="num">{pct(r.moneyline?.accuracy)}</td><td className="num">{pct(r.moneyline?.ece, 2)}</td>
                          <td className="num">{r.spread?.mae?.toFixed(2) ?? "–"}</td><td className="num">{r.spread?.mae_elo?.toFixed(2) ?? "–"}</td><td className="num">{r.total?.mae?.toFixed(2) ?? "–"}</td><td className="num">{r.total?.mae_baseline?.toFixed(2) ?? "–"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Scroll>
              </Card>
            )}

            <div className="grid gap-5 xl:grid-cols-2">
              <Card title="Kalibrierung" right={
                <select value={market} onChange={(e) => setMarket(e.target.value)} aria-label="Markt">
                  {[...PM, ...(Object.keys(m.game).length ? ["moneyline", "spread", "total"] : [])].map((k) => <option key={k} value={k}>{MARKET[k]}</option>)}
                </select>}>
                <CalibrationChart series={calib} ariaLabel={`Kalibrierung ${MARKET[market]}`} />
                <p className="mt-2 text-xs text-ink-3">ECE (Ensemble) {pct(m.calibration_ece[market], 2)} – fließt als Komponente „Kalibrierung“ in den Confidence-Score ein.</p>
              </Card>
              <Card title="Modellvergleich je Fold (Punkte-MAE, niedriger ist besser)">
                <LineChart ariaLabel="Punkte-MAE je Fold" series={foldSeries("points_mae")} yFmt={(v) => v.toFixed(2)} xFmt={(v) => m.folds[v - 1]?.start.slice(2, 7) ?? ""} />
                {m.folds.some((f: any) => f.lightgbm_home_brier != null) && (
                  <div className="mt-4"><div className="mb-1 text-sm font-medium">Moneyline-Brier je Fold</div>
                    <LineChart ariaLabel="Moneyline-Brier je Fold" series={foldSeries("home_brier")} yFmt={(v) => v.toFixed(3)} xFmt={(v) => m.folds[v - 1]?.start.slice(2, 7) ?? ""} /></div>
                )}
              </Card>
            </div>

            <Card title="Geschätzte Korrelationen für Kombinationswetten (Gauß-Copula, out-of-sample-Residuen)">
              <div className="grid gap-x-8 gap-y-1 text-sm md:grid-cols-2 xl:grid-cols-3">
                {Object.entries<number>(m.correlations).filter(([k]) => KEYS.some((p) => k === p[0])).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 border-b border-line py-1"><span className="text-ink-2">{KEYS.find((p) => p[0] === k)![1]}</span><span className="num">{v >= 0 ? "+" : ""}{v.toFixed(2)}</span></div>
                ))}
              </div>
            </Card>
          </div>
        )}
      </State>
    </>
  );
}
const fmtD = (d: string) => `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(2, 4)}`;
const KEYS: [string, string][] = [
  ["same|assists|points", "gleicher Spieler: Punkte ↔ Assists"], ["same|points|rebounds", "gleicher Spieler: Punkte ↔ Rebounds"],
  ["same|assists|rebounds", "gleicher Spieler: Rebounds ↔ Assists"], ["same|points|threes", "gleicher Spieler: Punkte ↔ Dreier"],
  ["same|points|pra", "gleicher Spieler: Punkte ↔ PRA"], ["mate|points|points", "Mitspieler: Punkte ↔ Punkte"],
  ["mate|assists|points", "Mitspieler: Punkte ↔ Assists"], ["mate|rebounds|rebounds", "Mitspieler: Rebounds ↔ Rebounds"],
  ["opp|points|points", "Gegenspieler: Punkte ↔ Punkte"], ["margin|points", "Punkte ↔ Margin des eigenen Teams"],
  ["total|points", "Punkte ↔ Gesamtpunkte"], ["game|margin|total", "Margin ↔ Gesamtpunkte"],
];
