"use client";
import Link from "next/link";
import { SRC, STATE, TONE } from "@/components/shell";
import { Badge, Card, PageTitle } from "@/components/ui";
import { age, money, spct, useApi } from "@/lib/api";

const LINKS = [
  ["/performance", "Performance", "Gewinne, Verluste, ROI, Trefferquote, Drawdown, CLV"],
  ["/backtest", "Backtesting", "Out-of-sample-Modellvergleich und Kalibrierung"],
  ["/settings", "Einstellungen", "Filter, Einsatzlimits, Wettsteuer, Jobs, Modelle"],
] as const;

export default function More() {
  const st = useApi<any>("status", 30000);
  const perf = useApi<any>("performance");
  return (
    <>
      <PageTitle title="Mehr" />
      <div className="space-y-3">
        <div className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
          {LINKS.map(([href, label, sub]) => (
            <Link key={href} href={href} className="flex items-center justify-between gap-3 px-4 py-3 active:bg-surface-2">
              <div><div className="font-medium">{label}</div><div className="text-xs text-ink-3">{sub}</div></div>
              <span className="text-ink-3" aria-hidden>›</span>
            </Link>
          ))}
        </div>
        {perf.data && (
          <Card title="Paper-Bankroll">
            <div className="flex items-baseline justify-between"><span className="num text-2xl font-semibold">{money(perf.data.bankroll)}</span>
              <span className="text-sm text-ink-2">ROI {spct(perf.data.roi)} · {perf.data.n_bets} Wetten</span></div>
          </Card>
        )}
        <Card title="Datenquellen">
          <div className="space-y-2 text-sm">
            {st.data?.sources.map((s: any) => (
              <div key={s.source} className="flex items-center justify-between gap-2">
                <span className="text-ink-2">{SRC[s.source] ?? s.source}</span>
                <Badge tone={TONE[s.state as keyof typeof TONE]}>{s.state === "ok" && s.age_s != null ? `vor ${age(s.age_s)}` : STATE[s.state as keyof typeof STATE]}</Badge>
              </div>
            ))}
          </div>
        </Card>
        <a href="/auth/logout" className="block rounded-xl border border-line bg-surface px-4 py-3 text-center text-sm text-ink-2">Abmelden</a>
        <p className="px-1 text-xs text-ink-3">Paper Betting – es werden keine Echtgeldwetten platziert. Glücksspiel kann süchtig machen: BZgA 0800 1 37 27 00.</p>
      </div>
    </>
  );
}
