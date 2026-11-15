"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, PageTitle, Scroll, State } from "@/components/ui";
import { FLAG, pct, time, useApi } from "@/lib/api";

export default function Games() {
  const [day, setDay] = useState<string>("");
  const { data, error, loading } = useApi<any>(`games${day ? `?day=${day}` : ""}`);
  return (
    <>
      <PageTitle title="Spielanalyse" sub="Spiele eines Tages mit Modellprognose. Klick auf eine Partie für Teams, Spieler, Matchups und Verletzungen."
        right={data && (
          <div className="flex items-center gap-2">
            <Button disabled={!data.prev_date} onClick={() => setDay(data.prev_date)}>← Vortag</Button>
            <input type="date" value={data.date} onChange={(e) => setDay(e.target.value)} aria-label="Datum" />
            <Button disabled={!data.next_date} onClick={() => setDay(data.next_date)}>Folgetag →</Button>
          </div>
        )} />
      <Card>
        <State loading={loading} error={error} empty={data?.games.length === 0 && "An diesem Tag sind keine Spiele geladen."}>
          <Scroll>
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
          </Scroll>
        </State>
      </Card>
    </>
  );
}
