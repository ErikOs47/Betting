"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { age, api, useApi } from "@/lib/api";
import { Badge, Button, Notice } from "./ui";

const NAV = [
  ["/", "Übersicht"], ["/value", "Value Finder"], ["/games", "Spielanalyse"], ["/builder", "Bet Builder"],
  ["/performance", "Performance"], ["/backtest", "Backtesting"], ["/settings", "Einstellungen"],
] as const;
// phone tab bar: four main destinations + "Mehr"
const TABS = [
  ["/", "Übersicht", "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"],
  ["/value", "Value", "M4 19V9m6 10V5m6 14v-7m4 7H2"],
  ["/games", "Spiele", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 0v18M3.5 9h17M3.5 15h17"],
  ["/builder", "Kombi", "M4 6h16M4 12h16M4 18h10"],
  ["/more", "Mehr", "M5 12h.01M12 12h.01M19 12h.01"],
] as const;
export const TONE = { ok: "good", stale: "warn", error: "bad", never_run: "warn", not_configured: "info" } as const;
export const STATE = { ok: "aktuell", stale: "veraltet", error: "Fehler", never_run: "nie geladen", not_configured: "nicht konfiguriert" } as const;
export const SRC: Record<string, string> = { "basketball-reference": "Spieldaten", "nba-injury-report": "Injury Report", "the-odds-api": "Quoten-Feed", sportsdataio: "SportsDataIO" };

const isOn = (path: string, href: string) =>
  href === "/" ? path === "/" : href === "/more" ? ["/more", "/performance", "/backtest", "/settings"].some((p) => path.startsWith(p)) : path.startsWith(href);

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const bare = path === "/login";
  const { data, reload } = useApi<any>(bare ? null : "status", 30000);
  useEffect(() => {
    if ("serviceWorker" in navigator && process.env.NODE_ENV === "production") navigator.serviceWorker.register("/sw.js").catch(() => {});
  }, []);
  if (bare) return <>{children}</>;
  const kill = data?.kill_switch?.active;
  const toggle = async () => {
    if (!kill && !confirm("Kill Switch aktivieren? Alle neuen Wetten und Einsatzvorschläge werden sofort blockiert.")) return;
    await api("kill-switch", { method: "POST", body: { active: !kill, reason: kill ? "" : "manuell im Dashboard" } });
    reload();
  };
  const sourcesOk = data?.sources?.filter((s: any) => s.configured).every((s: any) => s.state === "ok");
  return (
    <div className="min-h-screen lg:flex">
      {/* desktop sidebar */}
      <aside className="hidden border-r border-line bg-surface lg:sticky lg:top-0 lg:block lg:h-screen lg:w-56 lg:shrink-0">
        <div className="px-4 py-4">
          <div className="text-sm font-semibold tracking-wide text-ink">NBA Betting Intelligence</div>
          <div className="text-xs text-ink-3">Paper Betting · keine Echtgeldwetten</div>
        </div>
        <nav className="flex flex-col gap-1 px-2 pb-2" aria-label="Hauptnavigation">
          {NAV.map(([href, label]) => {
            const on = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} aria-current={on ? "page" : undefined}
                className={`rounded-md px-3 py-2 text-sm ${on ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:bg-surface-2"}`}>{label}</Link>
            );
          })}
        </nav>
        <div className="space-y-2 px-4 py-4 text-xs">
          <div className="uppercase tracking-wide text-ink-3">Datenquellen</div>
          {data?.sources?.map((s: any) => (
            <div key={s.source} className="flex items-center justify-between gap-2" title={s.last_error ?? ""}>
              <span className="text-ink-2">{SRC[s.source] ?? s.source}</span>
              <Badge tone={TONE[s.state as keyof typeof TONE]}>{s.state === "ok" && s.age_s != null ? `vor ${age(s.age_s)}` : STATE[s.state as keyof typeof STATE]}</Badge>
            </div>
          ))}
        </div>
      </aside>

      {/* phone top bar */}
      <header className="pt-safe sticky top-0 z-30 border-b border-line bg-bg/95 backdrop-blur lg:hidden">
        <div className="flex items-center justify-between gap-2 px-3 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className={`h-2 w-2 shrink-0 rounded-full ${sourcesOk ? "bg-good" : "bg-warn"}`} aria-label={sourcesOk ? "Datenquellen aktuell" : "Datenquelle prüfen"} />
            <span className="truncate text-sm font-semibold">NBA Betting Intelligence</span>
          </div>
          <button onClick={toggle} className={`shrink-0 rounded-md px-2.5 text-xs font-semibold ${kill ? "border border-bad text-ink" : "bg-bad text-white"}`}>
            {kill ? "Kill aktiv – aufheben" : "Kill Switch"}
          </button>
        </div>
      </header>

      <main className="min-w-0 flex-1 px-3 pb-24 pt-3 sm:px-4 lg:px-8 lg:pb-8 lg:pt-5">
        <div className="mb-4 hidden flex-wrap items-center justify-end gap-3 lg:flex">
          {kill ? <Badge tone="bad">Kill Switch aktiv</Badge> : <Badge tone="good">Wetten erlaubt (Paper)</Badge>}
          <Button kind={kill ? "default" : "danger"} onClick={toggle}>{kill ? "Kill Switch aufheben" : "Kill Switch"}</Button>
        </div>
        {kill && <div className="mb-3"><Notice tone="bad">Kill Switch aktiv seit {data.kill_switch.since?.slice(0, 16).replace("T", " ")} UTC – es werden keine Wetten angenommen.</Notice></div>}
        {data?.warnings?.filter((w: string) => !(w.startsWith("No odds API") && path !== "/")).length > 0 && (
          <div className="mb-3 space-y-2">
            {data.warnings.filter((w: string) => !(w.startsWith("No odds API") && path !== "/")).map((w: string) => (
              <Notice key={w} tone={w.startsWith("No odds API") ? "info" : "warn"}>{warnText(w)}</Notice>
            ))}
          </div>
        )}
        {children}
      </main>

      {/* phone tab bar */}
      <nav className="pb-safe fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 backdrop-blur lg:hidden" aria-label="Hauptnavigation mobil">
        <div className="grid grid-cols-5">
          {TABS.map(([href, label, d]) => {
            const on = isOn(path, href);
            return (
              <Link key={href} href={href} aria-current={on ? "page" : undefined}
                className={`flex flex-col items-center gap-0.5 pb-1 pt-2 text-[0.68rem] ${on ? "text-ink" : "text-ink-3"}`}>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={on ? 2.2 : 1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={d} /></svg>
                {label}
              </Link>
            );
          })}
        </div>
      </nav>
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
  "No odds API ": "Kein Quoten-Feed konfiguriert: Buchmacherquoten (z. B. Tipico) in der Spielanalyse von Hand erfassen – der Value Finder wertet sie aus.",
};
