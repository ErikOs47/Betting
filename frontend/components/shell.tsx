"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { age, api, useApi } from "@/lib/api";
import { Badge, Button, Notice } from "./ui";

const NAV = [
  ["/", "Übersicht"], ["/value", "Value Finder"], ["/games", "Spielanalyse"], ["/builder", "Bet Builder"],
  ["/performance", "Performance"], ["/backtest", "Backtesting"], ["/settings", "Einstellungen"],
] as const;
const TONE = { ok: "good", stale: "warn", error: "bad", never_run: "warn", not_configured: "info" } as const;
const STATE = { ok: "aktuell", stale: "veraltet", error: "Fehler", never_run: "nie geladen", not_configured: "nicht konfiguriert" } as const;
const SRC: Record<string, string> = { "basketball-reference": "Spieldaten", "nba-injury-report": "Injury Report", "the-odds-api": "Quoten", sportsdataio: "SportsDataIO" };

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { data, reload } = useApi<any>("status", 30000);
  const kill = data?.kill_switch?.active;
  const toggle = async () => {
    if (!kill && !confirm("Kill Switch aktivieren? Alle neuen Wetten und Einsatzvorschläge werden sofort blockiert.")) return;
    await api("kill-switch", { method: "POST", body: { active: !kill, reason: kill ? "" : "manuell im Dashboard" } });
    reload();
  };
  return (
    <div className="min-h-screen lg:flex">
      <aside className="border-b border-line bg-surface lg:sticky lg:top-0 lg:h-screen lg:w-56 lg:shrink-0 lg:border-b-0 lg:border-r">
        <div className="px-4 py-4">
          <div className="text-sm font-semibold tracking-wide text-ink">NBA Betting Intelligence</div>
          <div className="text-xs text-ink-3">Paper Betting · keine Echtgeldwetten</div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 lg:flex-col lg:overflow-visible" aria-label="Hauptnavigation">
          {NAV.map(([href, label]) => {
            const on = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} aria-current={on ? "page" : undefined}
                className={`rounded-md px-3 py-2 text-sm whitespace-nowrap ${on ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:bg-surface-2"}`}>
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="hidden space-y-2 px-4 py-4 text-xs lg:block">
          <div className="uppercase tracking-wide text-ink-3">Datenquellen</div>
          {data?.sources?.map((s: any) => (
            <div key={s.source} className="flex items-center justify-between gap-2" title={s.last_error ?? ""}>
              <span className="text-ink-2">{SRC[s.source] ?? s.source}</span>
              <Badge tone={TONE[s.state as keyof typeof TONE]}>{s.state === "ok" && s.age_s != null ? `vor ${age(s.age_s)}` : STATE[s.state as keyof typeof STATE]}</Badge>
            </div>
          ))}
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-5 lg:px-8">
        <div className="mb-4 flex flex-wrap items-center justify-end gap-3">
          {kill ? <Badge tone="bad">Kill Switch aktiv</Badge> : <Badge tone="good">Wetten erlaubt (Paper)</Badge>}
          <Button kind={kill ? "default" : "danger"} onClick={toggle}>{kill ? "Kill Switch aufheben" : "Kill Switch"}</Button>
        </div>
        {kill && <div className="mb-4"><Notice tone="bad">Kill Switch aktiv seit {data.kill_switch.since?.slice(0, 16).replace("T", " ")} UTC – es werden keine Wetten angenommen.</Notice></div>}
        {data?.warnings?.length > 0 && (
          <div className="mb-4 space-y-2">{data.warnings.map((w: string) => <Notice key={w} tone="warn">{warnText(w)}</Notice>)}</div>
        )}
        {children}
      </main>
    </div>
  );
}

function warnText(w: string): string {
  const m = w.match(/^(\d+) past games/);
  if (m) return `${m[1]} vergangene Spiele haben noch kein Ergebnis: Prognosen sind deaktiviert, bis die Ergebnisse geladen sind.`;
  return WARN[w.slice(0, 12)] ?? w;
}

const WARN: Record<string, string> = {
  "Injury repor": "Injury Report fehlt oder ist veraltet: Datenqualität und Confidence werden reduziert.",
  "No odds API ": "Kein Odds-API-Schlüssel konfiguriert: Der Value Finder hat keine Buchmacherquoten. Manuelle Quoteneingabe (Spielanalyse, Bet Builder) funktioniert.",
};
