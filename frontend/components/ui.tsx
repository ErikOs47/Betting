"use client";
import type { ReactNode } from "react";

export function Card({ title, children, right, className = "" }: { title?: ReactNode; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-surface ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" }) {
  const mark = tone === "good" ? "▲" : tone === "bad" ? "▼" : "";
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="text-xs uppercase tracking-wide text-ink-3">{label}</div>
      <div className="num mt-1 text-2xl font-semibold text-ink">
        {mark && <span className={`mr-1 text-sm ${tone === "good" ? "text-good" : "text-bad"}`} aria-hidden>{mark}</span>}
        {value}
      </div>
      {sub && <div className="mt-1 text-xs text-ink-3">{sub}</div>}
    </div>
  );
}

const TONES = {
  good: "border-good/50 text-ink", warn: "border-warn/60 text-ink", bad: "border-bad/60 text-ink", info: "border-line text-ink-2",
} as const;
const DOT = { good: "bg-good", warn: "bg-warn", bad: "bg-bad", info: "bg-ink-3" } as const;

export function Badge({ tone = "info", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs whitespace-nowrap ${TONES[tone]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT[tone]}`} aria-hidden />
      {children}
    </span>
  );
}

export function Button({ children, onClick, kind = "default", disabled, title, type = "button" }: {
  children: ReactNode; onClick?: () => void; kind?: "default" | "primary" | "danger"; disabled?: boolean; title?: string; type?: "button" | "submit";
}) {
  const k = kind === "primary" ? "bg-s1 text-white hover:brightness-110 border-transparent"
    : kind === "danger" ? "bg-bad text-white hover:brightness-110 border-transparent"
    : "bg-surface-2 text-ink hover:bg-line border-line";
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title}
      className={`rounded-md border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${k}`}>
      {children}
    </button>
  );
}

export function Notice({ tone = "info", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return (
    <div role={tone === "bad" ? "alert" : "status"} className={`flex items-start gap-2 rounded-lg border bg-surface px-3 py-2 text-sm ${TONES[tone]}`}>
      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${DOT[tone]}`} aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function State({ loading, error, empty, children }: { loading?: boolean; error?: string | null; empty?: string | false; children?: ReactNode }) {
  if (error) return <Notice tone="bad">Fehler: {error}</Notice>;
  if (loading) return <div className="py-8 text-center text-sm text-ink-3">Lade …</div>;
  if (empty) return <div className="py-8 text-center text-sm text-ink-3">{empty}</div>;
  return <>{children}</>;
}

export function Scroll({ children }: { children: ReactNode }) {
  return <div className="-mx-4 overflow-x-auto px-4">{children}</div>;
}

export function PageTitle({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {sub && <p className="mt-1 max-w-3xl text-sm text-ink-3">{sub}</p>}
      </div>
      {right}
    </div>
  );
}
