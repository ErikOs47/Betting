"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, DesktopOnly, MobileList, PageTitle, Scroll, State } from "@/components/ui";
import { FLAG, pct, time, useApi } from "@/lib/api";

export default function Games() {
  const [day, setDay] = useState<string>("");
  const { data, error, loading } = useApi<any>(`games${day ? `?day=${day}` : ""}`);
  return (
    <>
      <PageTitle title="Spielanalyse" sub="Spiele eines Tages mit Modellprognose. Klick auf eine Partie für Teams, Spieler, Matchups und Verletzungen."
        right={data && (
          <div className="flex w-full items-center gap-2 sm:w-auto">
            <Button disabled={!data.prev_date} onClick={() => setDay(data.prev_date)}>←<span className="hidden sm:inline"> Vortag</span></Button>
            <input className="min-w-0 flex-1" type="date" value={data.date} onChange={(e) => setDay(e.target.value)} aria-label="Datum" />
            <Button disabled={!data.next_date} onClick={() => setDay(data.next_date)}><span className="hidden sm:inline">Folgetag </span>→</Button>
          </div>
        )} />
      <Card>
        <State loading={loading} error={error} empty={data?.games.length === 0 && "An diesem Tag sind keine Spiele geladen."}>
          <MobileList>
            {data?.games.map((g: any) => (
              <Link key={g.id} href={`/games/${g.id}`} className="block px-3 py-2.5 active:bg-surface-2">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0"><div className="font-medium">{g.away} @ {g.home}</div><div className="text-xs text-ink-3">{time(g.tip_utc)}</div></div>
                  <div className="num shrink-0 text-right text-sm">{g.status === "final" ? `${g.away_pts}:${g.home_pts}` : g.model ? `${g.home} ${pct(g.model.home_win_prob, 0)}` : "–"}</div>
                </div>
                {g.model && g.status !== "final" && (
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-2">
                    <span className="num">fair {g.model.fair_home.toFixed(2)} / {g.model.fair_away.toFixed(2)}</span>
                    <span className="num">Margin {g.model.margin > 0 ? "+" : ""}{g.model.margin}</span><span className="num">O/U {g.model.total}</span>
                    {!g.model.enabled && <Badge tone="warn">deaktiviert</Badge>}
                  </div>
                )}
              </Link>
            ))}
          </MobileList>
          <DesktopOnly><Scroll>
            <table>
              <thead><tr><th>Tip-off</th><th>Partie</th><th>Heimsieg</th><th>Faire Quote Heim / Auswärts</th><th>Margin (Heim)</th><th>Total</th><th>Datenqualität</th><th>Ergebnis</th></tr></thead>
              <tbody>
                {data?.games.map((g: any) => (
                  <tr key={g.id}>
                    <td className="num text-ink-2">{time(g.tip_utc)}</td>
                    <td><Link href={`/games/${g.id}`} className="font-medium hover:underline">{g.away_name} @ {g.home_name}</Link></td>
                    <td className="num">{g.model ? pct(g.model.home_win_prob) : "–"}</td>
                    <td className="num">{g.model ? `${g.model.fair_home.toFixed(2)} / ${g.model.fair_away.toFixed(2)}` : "–"}</td>
                    <td className="num">{g.model ? `${g.model.margin > 0 ? "+" : ""}${g.model.margin} ± ${g.model.sigma_margin.toFixed(1)}` : "–"}</td>
                    <td className="num">{g.model ? `${g.model.total} ± ${g.model.sigma_total.toFixed(1)}` : "–"}</td>
                    <td>{g.model ? <div className="flex flex-wrap gap-1"><Badge tone={g.model.enabled ? "good" : "warn"}>{pct(g.model.data_quality, 0)}</Badge>{g.model.flags.map((f: string) => <Badge key={f}>{FLAG[f] ?? f}</Badge>)}</div> : "–"}</td>
                    <td className="num">{g.status === "final" ? `${g.away_pts}:${g.home_pts}` : "offen"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scroll></DesktopOnly>
        </State>
      </Card>
    </>
  );
}
