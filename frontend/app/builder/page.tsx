"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Notice, PageTitle, Scroll, State, Stat } from "@/components/ui";
import { api, money, pct, spct, useSlip } from "@/lib/api";

export default function Builder() {
  const slip = useSlip();
  const [offered, setOffered] = useState("");
  const [stake, setStake] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const body = () => ({ legs: slip.legs.map(({ label, ...l }) => l), offered_price: offered ? Number(offered) : null, stake: stake ? Number(stake) : null });
  const key = JSON.stringify(slip.legs) + offered;
  useEffect(() => {
    setRes(null); setErr(null);
    if (!slip.legs.length) return;
    const t = setTimeout(() => api("parlay/price", { method: "POST", body: body() }).then(setRes).catch((e) => setErr(e.message)), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const place = async () => {
    setMsg(null); setErr(null);
    try {
      const b = await api("bets", { method: "POST", body: body() });
      setMsg(`Paper-Wette #${b.id} platziert · Einsatz ${money(b.stake)} @ ${b.price.toFixed(2)}`);
      slip.clear();
    } catch (e) { setErr((e as Error).message); }
  };
  const single = slip.legs.length === 1;
  return (
    <>
      <PageTitle title="Bet Builder" sub="Einzel- und Kombinationswetten. Die gemeinsame Wahrscheinlichkeit wird per Monte-Carlo-Simulation (Gauß-Copula, 100 000 Ziehungen) mit im Backtest geschätzten Korrelationen berechnet – nicht als einfaches Produkt." />
      {msg && <div className="mb-4"><Notice tone="good">{msg}</Notice></div>}
      {err && <div className="mb-4"><Notice tone="bad">{err}</Notice></div>}
      <div className="grid gap-5 xl:grid-cols-5">
        <Card title={`Bet Slip (${slip.legs.length})`} className="xl:col-span-3" right={slip.legs.length > 0 && <Button onClick={slip.clear}>Leeren</Button>}>
          <State empty={slip.legs.length === 0 && false}>
            {slip.legs.length === 0 ? (
              <div className="py-8 text-center text-sm text-ink-3">Noch keine Auswahl. Füge Wetten im <Link className="text-s1 underline" href="/value">Value Finder</Link> oder über „Quote prüfen“ in der <Link className="text-s1 underline" href="/games">Spielanalyse</Link> hinzu.</div>
            ) : (
              <Scroll>
                <table>
                  <thead><tr><th>Auswahl</th><th>Quote</th><th>Modell</th><th>Simulation</th><th></th></tr></thead>
                  <tbody>
                    {slip.legs.map((l, i) => {
                      const r = res?.legs?.[i];
                      return (
                        <tr key={i}>
                          <td>{r?.description ?? l.label}{r && !r.enabled && <span className="ml-2"><Badge tone="warn">Prognose deaktiviert</Badge></span>}</td>
                          <td><input className="num w-20" inputMode="decimal" aria-label="Quote" value={l.price ?? ""} onChange={(e) => slip.update(i, { price: e.target.value ? Number(e.target.value) : null })} /></td>
                          <td className="num">{pct(r?.model_prob)}</td><td className="num">{pct(r?.sim_prob)}</td>
                          <td><Button onClick={() => slip.remove(i)} title="Entfernen">✕</Button></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Scroll>
            )}
          </State>
          {slip.legs.length > 1 && (
            <div className="mt-4 flex flex-wrap items-end gap-3 text-sm">
              <label className="grid gap-1">Angebotene Kombiquote (z. B. Same Game Parlay)<input className="w-32" inputMode="decimal" value={offered} onChange={(e) => setOffered(e.target.value)} placeholder={res?.product_price ? `Produkt ${res.product_price.toFixed(2)}` : "Quote"} /></label>
            </div>
          )}
        </Card>
        <div className="space-y-3 xl:col-span-2">
          {res && !single && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Gemeinsame Wahrscheinlichkeit" value={pct(res.joint_prob, 2)} sub={`± ${pct(res.joint_prob_se, 2)} Simulationsfehler`} />
                <Stat label="Bei Unabhängigkeit" value={pct(res.independent_prob, 2)} sub={`Korrelationsfaktor ×${res.correlation_factor?.toFixed(2) ?? "–"}`} />
                <Stat label="Faire Kombiquote" value={res.fair_odds?.toFixed(2) ?? "–"} sub={res.price ? `angeboten ${res.price.toFixed(2)}` : "keine Quote eingegeben"} />
                <Stat label="Expected Value" value={spct(res.ev)} tone={res.ev > 0 ? "good" : res.ev < 0 ? "bad" : undefined} sub={res.stake && !res.stake.blocked ? `Einsatz ${money(res.stake.amount)}` : res.stake?.reasons?.[0]} />
              </div>
              {res.warning && <Notice tone="warn">Korrelationen noch nicht geschätzt (Backtest ausführen) – Legs werden als unabhängig behandelt.</Notice>}
              {res.same_game && res.correlations_available && <Notice>Same-Game-Kombination: Korrelationen zwischen den Legs sind berücksichtigt (Matrix: {res.correlation_matrix.map((r: number[]) => r.map((v) => v.toFixed(2)).join(" ")).join(" | ")}).</Notice>}
              {res.prob_with_push > 0.001 && <Notice>Zusätzlich {pct(res.prob_with_push, 2)} Wahrscheinlichkeit für Gewinn mit mindestens einem Push (reduzierte Auszahlung, nicht im EV enthalten).</Notice>}
            </>
          )}
          {res && single && <Notice>Einzelwette: Modellwahrscheinlichkeit {pct(res.legs[0].model_prob)} · faire Quote {res.fair_odds?.toFixed(2)} · EV {spct(res.ev)}</Notice>}
          {slip.legs.length > 0 && (
            <Card title="Paper-Wette platzieren">
              <div className="flex flex-wrap items-end gap-3 text-sm">
                <label className="grid gap-1">Einsatz (leer = ¼ Kelly mit Limits)<input className="w-32" inputMode="decimal" value={stake} onChange={(e) => setStake(e.target.value)} placeholder={res?.stake && !res.stake.blocked ? String(res.stake.amount) : "auto"} /></label>
                <Button kind="primary" onClick={place} disabled={!res || !(res.ev > 0)}>Platzieren</Button>
              </div>
              <p className="mt-2 text-xs text-ink-3">Nur Wetten mit positivem Erwartungswert und aktiver Prognose werden angenommen. Limits: pro Wette, pro Partie und Tagesverlust (siehe Einstellungen).</p>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
