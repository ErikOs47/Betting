"use client";
import Link from "next/link";
import { LineChart, SERIES } from "@/components/charts";
import { Badge, Card, DesktopOnly, MobileList, PageTitle, Scroll, Stat, State } from "@/components/ui";
import { MARKET, SEL, money, pct, spct, time, useApi } from "@/lib/api";

export default function Overview() {
  const { data, error, loading } = useApi<any>("overview", 60000);
  const b = data?.bankroll;
  return (
    <>
      <PageTitle title="Übersicht" sub="Spiele, Bankroll und aktuelle Value Bets. Alle Zahlen stammen aus gespeicherten Prognosen und Paper-Wetten." />
      <State loading={loading} error={error}>
        {data && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Bankroll" value={money(b.bankroll)} sub={`Start ${money(b.start_bankroll)} · offen ${money(b.open_stake)}`} />
              <Stat label="ROI (auf Startkapital)" value={spct(b.roi)} tone={b.roi > 0 ? "good" : b.roi < 0 ? "bad" : undefined}
                sub={`Yield ${spct(b.yield)} · ${b.n_bets} abgerechnete Wetten`} />
              <Stat label="Trefferquote" value={pct(b.hit_rate)} sub={`Max. Drawdown ${pct(b.max_drawdown)} · Ø CLV ${spct(b.avg_clv)}`} />
              <Stat label="Value Bets (Filter erfüllt)" value={data.value_bets.count} sub={<Link className="text-s1 underline" href="/value">zum Value Finder</Link>} />
            </div>
            <div className="grid gap-5 xl:grid-cols-2">
              <Card title={`Spiele am ${new Date(data.games_date + "T12:00:00").toLocaleDateString("de-DE")}`} right={<Link className="text-xs text-s1 underline" href="/games">alle Spiele</Link>}>
                <State empty={data.games.length === 0 && "Keine Spiele geladen."}>
                  <MobileList>
                    {data.games.map((g: any) => (
                      <Link key={g.id} href={`/games/${g.id}`} className="flex items-center justify-between gap-3 px-3 py-2.5 active:bg-surface-2">
                        <div className="min-w-0"><div className="font-medium">{g.away} @ {g.home}</div><div className="text-xs text-ink-3">{time(g.tip_utc)}{g.model && !g.model.enabled ? " · Prognose deaktiviert" : ""}</div></div>
                        <div className="num shrink-0 text-right text-sm">{g.status === "final" ? `${g.away_pts}:${g.home_pts}` : g.model ? <>{g.home} {pct(g.model.home_win_prob, 0)}<div className="text-xs text-ink-3">{g.model.margin > 0 ? "+" : ""}{g.model.margin} · O/U {g.model.total}</div></> : "–"}</div>
                      </Link>
                    ))}
                  </MobileList>
                  <DesktopOnly><Scroll>
                    <table>
                      <thead><tr><th>Tip-off</th><th>Partie</th><th>Heimsieg</th><th>Margin</th><th>Total</th><th>Status</th></tr></thead>
                      <tbody>
                        {data.games.map((g: any) => (
                          <tr key={g.id}>
                            <td className="num text-ink-2">{time(g.tip_utc)}</td>
                            <td><Link className="font-medium hover:underline" href={`/games/${g.id}`}>{g.away} @ {g.home}</Link></td>
                            <td className="num">{g.model ? pct(g.model.home_win_prob) : "–"}</td>
                            <td className="num">{g.model ? `${g.model.margin > 0 ? "+" : ""}${g.model.margin}` : "–"}</td>
                            <td className="num">{g.model?.total ?? "–"}</td>
                            <td>{g.status === "final" ? <span className="num text-ink-2">{g.away_pts}:{g.home_pts}</span>
                              : g.model ? (g.model.enabled ? <Badge tone="good">Prognose aktiv</Badge> : <Badge tone="warn">deaktiviert</Badge>) : <Badge>keine Prognose</Badge>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Scroll></DesktopOnly>
                </State>
              </Card>
              <Card title="Bankroll-Verlauf (Paper)">
                {data.equity.length > 1 ? (
                  <LineChart ariaLabel="Bankroll-Verlauf" series={[{ label: "Bankroll", color: SERIES[0], points: data.equity.map((e: any, i: number) => ({ x: i + 1, y: e.equity })) }]}
                    yFmt={(v) => `${v.toFixed(0)} €`} xFmt={(v) => `#${v}`} refLine={b.start_bankroll} />
                ) : <div className="py-10 text-center text-sm text-ink-3">Noch keine abgerechneten Paper-Wetten.</div>}
              </Card>
            </div>
            <Card title="Top Value Bets">
              <State empty={data.value_bets.count === 0 && "Aktuell erfüllt keine Wette alle Filter (oder es liegen keine Quoten vor)."}>
                <MobileList>
                  {data.value_bets.top.map((c: any, i: number) => (
                    <Link key={i} href="/value" className="flex items-center justify-between gap-3 px-3 py-2.5">
                      <div className="min-w-0"><div className="truncate font-medium">{c.player_name ?? c.matchup}</div><div className="truncate text-xs text-ink-2">{MARKET[c.market]} {SEL[c.selection]} {c.line ?? ""} · {c.bookmaker} {c.price.toFixed(2)}</div></div>
                      <div className="num shrink-0 text-right text-sm">EV {spct(c.ev)}<div className="text-xs text-ink-3">{c.stake.blocked ? "–" : money(c.stake.amount)}</div></div>
                    </Link>
                  ))}
                </MobileList>
                <DesktopOnly><Scroll>
                  <table>
                    <thead><tr><th>Partie</th><th>Wette</th><th>Quote</th><th>Modell</th><th>Edge</th><th>EV</th><th>Confidence</th><th>Einsatz</th></tr></thead>
                    <tbody>
                      {data.value_bets.top.map((c: any, i: number) => (
                        <tr key={i}>
                          <td>{c.matchup}</td>
                          <td>{c.player_name ? `${c.player_name} · ` : ""}{MARKET[c.market]} {SEL[c.selection]} {c.line ?? ""}</td>
                          <td className="num">{c.price.toFixed(2)} <span className="text-ink-3">{c.bookmaker}</span></td>
                          <td className="num">{pct(c.model_prob)}</td><td className="num">{spct(c.edge)}</td><td className="num">{spct(c.ev)}</td>
                          <td className="num">{c.confidence}</td><td className="num">{c.stake.blocked ? "–" : money(c.stake.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Scroll></DesktopOnly>
              </State>
            </Card>
          </div>
        )}
      </State>
    </>
  );
}
