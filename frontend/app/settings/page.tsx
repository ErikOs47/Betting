"use client";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Notice, PageTitle, Scroll, State } from "@/components/ui";
import { age, api, time, useApi } from "@/lib/api";

type Field = { key: string; label: string; pct?: boolean; hint?: string };
const FILTERS: Field[] = [
  { key: "min_edge", label: "Mindest-Edge", pct: true, hint: "Modell − faire Marktwahrscheinlichkeit" },
  { key: "min_confidence", label: "Mindest-Confidence (0–100)" },
  { key: "min_ev", label: "Mindest-EV", pct: true }, { key: "min_data_quality", label: "Mindest-Datenqualität", pct: true },
  { key: "max_edge", label: "Maximal plausibler Edge", pct: true, hint: "darüber meist fehlende Information" },
  { key: "min_price", label: "Mindestquote" }, { key: "max_price", label: "Höchstquote" },
  { key: "payout_tax", label: "Wettsteuer auf die Auszahlung", pct: true, hint: "z. B. 5,3 bei deutschen Anbietern, die die Steuer im Gewinnfall abziehen; EV und Einsatz rechnen mit der Netto-Quote" },
];
const RISK: Field[] = [
  { key: "kelly_fraction", label: "Kelly-Anteil", hint: "0,25 = ¼ Kelly" }, { key: "max_bet_pct", label: "Max. Einsatz pro Wette", pct: true },
  { key: "max_game_pct", label: "Max. Einsatz pro Partie", pct: true }, { key: "max_daily_loss_pct", label: "Max. Tagesverlust", pct: true },
  { key: "uncertainty_shrink", label: "Modellgewicht bei Einsatzberechnung", hint: "1 = Modell voll, 0,5 = Mittel aus Modell und Markt" },
  { key: "min_stake", label: "Mindesteinsatz (€)" },
];
const AUTO: Field[] = [{ key: "odds_interval_min", label: "Quoten-Abruf alle … Minuten" }, { key: "props_hours_before_tip", label: "Spieler-Props ab … Stunden vor Tip-off" },
  { key: "manual_odds_max_age_min", label: "Manuell erfasste Quoten gelten … Minuten" }];
const JOBS: [string, string][] = [["results", "Ergebnisse & Spielplan"], ["injuries", "Injury Report"], ["odds", "Quoten"], ["predict", "Prognosen"], ["settle", "Wetten abrechnen"], ["train", "Modelle neu trainieren"], ["backtest", "Backtest"], ["all", "Alles aktualisieren"]];

function Group({ title, name, fields, values, onSaved }: { title: string; name: string; fields: Field[]; values: any; onSaved: () => void }) {
  const [form, setForm] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { setForm(Object.fromEntries(fields.map((f) => [f.key, String(f.pct ? +(values[f.key] * 100).toFixed(4) : values[f.key])]))); }, [values, fields]);
  const save = async () => {
    try {
      await api(`settings/${name}`, { method: "PUT", body: Object.fromEntries(fields.map((f) => [f.key, f.pct ? Number(form[f.key]) / 100 : Number(form[f.key])])) });
      setMsg({ ok: true, text: "Gespeichert." }); onSaved();
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };
  return (
    <Card title={title} right={<Button kind="primary" onClick={save}>Speichern</Button>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((f) => (
          <label key={f.key} className="grid gap-1 text-sm">
            <span>{f.label}{f.pct ? " (%)" : ""}</span>
            <input inputMode="decimal" value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
            {f.hint && <span className="text-xs text-ink-3">{f.hint}</span>}
          </label>
        ))}
      </div>
      {msg && <div className="mt-3"><Notice tone={msg.ok ? "good" : "bad"}>{msg.text}</Notice></div>}
    </Card>
  );
}

export default function Settings() {
  const s = useApi<any>("settings");
  const st = useApi<any>("status", 15000);
  const jobs = useApi<any>("jobs", 5000);
  const models = useApi<any>("models");
  const start = async (name: string) => { await api(`jobs/${name}`, { method: "POST" }); jobs.reload(); };
  return (
    <>
      <PageTitle title="Einstellungen" sub="Filter, Einsatzlimits, Datenquellen und Automatisierung. API-Schlüssel werden ausschließlich serverseitig per Umgebungsvariable gesetzt und nie an den Browser übertragen." />
      <State loading={s.loading} error={s.error}>
        {s.data && (
          <div className="space-y-5">
            <div className="grid gap-5 xl:grid-cols-2">
              <Group title="Value-Filter" name="filters" fields={FILTERS} values={s.data.filters} onSaved={s.reload} />
              <Group title="Risikomanagement" name="risk" fields={RISK} values={s.data.risk} onSaved={s.reload} />
            </div>
            <div className="grid gap-5 xl:grid-cols-2">
              <Card title="Datenquellen">
                <Scroll>
                  <table>
                    <thead><tr><th>Quelle</th><th>Status</th><th>Letzter Erfolg</th><th>Hinweis</th></tr></thead>
                    <tbody>
                      {st.data?.sources.map((x: any) => (
                        <tr key={x.source}>
                          <td>{x.source}</td>
                          <td><Badge tone={x.state === "ok" ? "good" : x.state === "error" ? "bad" : x.state === "not_configured" ? "info" : "warn"}>{x.state}</Badge></td>
                          <td className="num text-ink-2">{x.age_s != null ? `vor ${age(x.age_s)}` : "–"}</td>
                          <td className="text-xs text-ink-3">{x.last_error ?? (x.configured ? "" : "Schlüssel fehlt (.env)")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Scroll>
                <div className="mt-3 space-y-1 text-xs text-ink-2">
                  <div>The Odds API: Schlüssel {s.data.providers.odds_api_key_set ? "gesetzt" : "nicht gesetzt"} · Regionen {s.data.providers.odds_regions} · Credits diesen Monat {st.data?.odds_credits.used_this_month ?? 0} / Budget {s.data.providers.odds_monthly_credit_budget}</div>
                  <div>SportsDataIO: Schlüssel {s.data.providers.sportsdataio_key_set ? "gesetzt" : "nicht gesetzt"} (optional)</div>
                  <div>Daten: {st.data?.data.final_games} Spiele · Saisons {st.data?.data.seasons.join(", ")} · letztes Ergebnis {st.data?.data.last_final_game_date} · {st.data?.data.odds_snapshots} Quoten-Snapshots</div>
                </div>
              </Card>
              <div className="space-y-5">
                <Group title="Automatisierung" name="automation" fields={AUTO} values={s.data.automation} onSaved={s.reload} />
                <Card title="Jobs manuell starten">
                  <div className="flex flex-wrap gap-2">{JOBS.map(([k, label]) => <Button key={k} onClick={() => start(k)} disabled={jobs.data?.jobs[k]?.running}>{jobs.data?.jobs[k]?.running ? `${label} läuft …` : label}</Button>)}</div>
                  <div className="mt-3 space-y-1 text-xs text-ink-3">
                    {Object.entries<any>(jobs.data?.jobs ?? {}).filter(([, v]) => !v.running).map(([k, v]) => (
                      <div key={k}><b className="text-ink-2">{k}</b> {time(v.finished)}: {JSON.stringify(v.result).slice(0, 260)}</div>
                    ))}
                  </div>
                </Card>
              </div>
            </div>
            <Card title="Modellversionen">
              <State empty={models.data?.models.length === 0 && "Noch keine Modelle trainiert."}>
                <Scroll>
                  <table>
                    <thead><tr><th>Version</th><th>Typ</th><th>Algorithmus</th><th>Training</th><th>Zeilen</th><th>Features</th><th>Daten-Hash</th><th>Code</th><th>Status</th></tr></thead>
                    <tbody>
                      {models.data?.models.map((m: any) => (
                        <tr key={m.id}><td className="num text-xs">{m.version}</td><td>{m.family === "player" ? "Spieler" : "Spiel"}</td><td>{m.algo}</td><td className="num">{m.train_start} – {m.train_end}</td>
                          <td className="num">{m.n_train.toLocaleString("de-DE")}</td><td className="num">{m.n_features}</td><td className="num text-xs">{m.data_hash}</td><td className="num text-xs">{m.code_sha.slice(0, 7)}</td>
                          <td>{m.active ? <Badge tone="good">aktiv</Badge> : <Badge>archiviert</Badge>}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </Scroll>
              </State>
            </Card>
          </div>
        )}
      </State>
    </>
  );
}
