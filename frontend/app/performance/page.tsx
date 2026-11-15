"use client";
import { useState } from "react";
import { LineChart, SERIES } from "@/components/charts";
import { Badge, Button, Card, Notice, PageTitle, Scroll, Stat, State } from "@/components/ui";
import { MARKET, api, money, pct, spct, time, useApi } from "@/lib/api";

const ST: Record<string, ["good" | "bad" | "info" | "warn", string]> = {
  open: ["info", "offen"], won: ["good", "gewonnen"], lost: ["bad", "verloren"], push: ["warn", "Push"], void: ["warn", "storniert"],
};

export default function Performance() {
  const perf = useApi<any>("performance", 60000);
  const bets = useApi<any>("bets", 60000);
  const [msg, setMsg] = useState<string | null>(null);
  const settle = async () => {
    const r = await api("bets/settle", { method: "POST" });
    setMsg(`${r.settled} Wette(n) abgerechnet.`);
    perf.reload(); bets.reload();
  };
  const p = perf.data;
  return (
    <>
      <PageTitle title="Performance" sub="Ergebnisse der Paper-Wetten. Yield = Gewinn / Umsatz, ROI = Gewinn / Startkapital, CLV = Wettquote × faire Schlusswahrscheinlichkeit − 1."
        right={<Button onClick={settle}>Offene Wetten abrechnen</Button>} />
      {msg && <div className="mb-4"><Notice tone="good">{msg}</Notice></div>}
      <State loading={perf.loading} error={perf.error}>
        {p && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Gewinn / Verlust" value={money(p.profit)} tone={p.profit > 0 ? "good" : p.profit < 0 ? "bad" : undefined} sub={`Umsatz ${money(p.turnover)}`} />
              <Stat label="ROI · Yield" value={`${spct(p.roi)} · ${spct(p.yield)}`} sub={`Bankroll ${money(p.bankroll)}`} />
              <Stat label="Trefferquote" value={pct(p.hit_rate)} sub={`${p.won} gewonnen · ${p.lost} verloren · ${p.push_void} Push/Storno`} />
              <Stat label="Max. Drawdown" value={pct(p.max_drawdown)} sub={`Ø CLV ${spct(p.avg_clv)} · CLV positiv ${pct(p.clv_positive_share, 0)}`} />
            </div>
            <Card title="Bankroll-Verlauf">
              {p.equity.length > 1 ? <LineChart ariaLabel="Bankroll-Verlauf" yFmt={(v) => `${v.toFixed(0)} €`} xFmt={(v) => `#${v}`} refLine={p.start_bankroll}
                series={[{ label: "Bankroll", color: SERIES[0], points: p.equity.map((e: any, i: number) => ({ x: i + 1, y: e.equity })) }]} />
                : <div className="py-10 text-center text-sm text-ink-3">Noch keine abgerechneten Wetten. Der Verlauf erscheint nach den ersten Abrechnungen.</div>}
            </Card>
            {Object.keys(p.by_market).length > 0 && (
              <Card title="Nach Markt">
                <Scroll><table><thead><tr><th>Markt</th><th>Wetten</th><th>Gewonnen</th><th>Umsatz</th><th>Gewinn</th><th>Yield</th></tr></thead>
                  <tbody>{Object.entries<any>(p.by_market).map(([m, d]) => <tr key={m}><td>{MARKET[m] ?? m}</td><td className="num">{d.n}</td><td className="num">{d.won}</td><td className="num">{money(d.stake)}</td><td className="num">{money(d.pnl)}</td><td className="num">{spct(d.yield)}</td></tr>)}</tbody></table></Scroll>
              </Card>
            )}
          </div>
        )}
      </State>
      <div className="mt-5">
        <Card title="Wetten">
          <State loading={bets.loading} error={bets.error} empty={bets.data?.bets.length === 0 && "Noch keine Paper-Wetten platziert."}>
            <Scroll>
              <table>
                <thead><tr><th>#</th><th>Platziert</th><th>Wette</th><th>Einsatz</th><th>Quote</th><th>Modell</th><th>EV</th><th>Status</th><th>G/V</th><th>CLV</th></tr></thead>
                <tbody>
                  {bets.data?.bets.map((b: any) => (
                    <tr key={b.id}>
                      <td className="num text-ink-3">{b.id}</td><td className="num text-ink-2">{time(b.placed_at)}</td><td>{b.description}</td>
                      <td className="num">{money(b.stake)}</td><td className="num">{b.price.toFixed(2)}</td><td className="num">{pct(b.model_prob)}</td><td className="num">{spct(b.ev)}</td>
                      <td><Badge tone={ST[b.status][0]}>{ST[b.status][1]}</Badge></td><td className="num">{b.pnl == null ? "–" : money(b.pnl)}</td><td className="num">{spct(b.clv)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scroll>
          </State>
        </Card>
      </div>
    </>
  );
}
