"use client";
// Small dependency-free SVG charts: one axis, thin marks, recessive grid, hover tooltip, direct labels.
import { useState, type ReactNode } from "react";

const C = { s1: "#3987e5", s2: "#d95926", s3: "#199e70", grid: "#33332f", ink2: "#c3c2b7", ink3: "#8f8e85", surface: "#1a1a19" };
export const SERIES = [C.s1, C.s2, C.s3];

function ticks(lo: number, hi: number, n = 4): number[] {
  if (hi === lo) return [lo];
  const step0 = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? step0;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

function Tip({ x, y, w, children }: { x: number; y: number; w: number; children: ReactNode }) {
  return (
    <div className="pointer-events-none absolute z-10 rounded-md border border-line bg-surface-2 px-2 py-1 text-xs text-ink shadow-lg"
      style={{ left: `${(x / w) * 100}%`, top: y, transform: `translate(${x > w * 0.6 ? "-105%" : "5%"}, -50%)`, whiteSpace: "nowrap" }}>
      {children}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          <svg width="16" height="8" aria-hidden><line x1="0" y1="4" x2="16" y2="4" stroke={i.color} strokeWidth="2" strokeDasharray={i.dashed ? "3 3" : undefined} /></svg>
          {i.label}
        </span>
      ))}
    </div>
  );
}

export type Series = { label: string; color: string; points: { x: number; y: number }[]; dashed?: boolean };

/** Line chart on a numeric x axis. xLabel(x) formats ticks and tooltips. */
export function LineChart({ series, height = 220, yFmt = (v) => String(v), xFmt = (v) => String(v), yDomain, xTicks, refLine, ariaLabel }: {
  series: Series[]; height?: number; yFmt?: (v: number) => string; xFmt?: (v: number) => string; yDomain?: [number, number];
  xTicks?: number[]; refLine?: number; ariaLabel: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = height, m = { l: 52, r: 14, t: 10, b: 26 };
  const all = series.flatMap((s) => s.points);
  if (!all.length) return <div className="py-8 text-center text-sm text-ink-3">Keine Daten</div>;
  const xs = all.map((p) => p.x), ys = all.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let [y0, y1] = yDomain ?? [Math.min(...ys, refLine ?? Infinity), Math.max(...ys, refLine ?? -Infinity)];
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  if (!yDomain) { const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad; }
  const sx = (x: number) => m.l + ((x - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const sy = (y: number) => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);
  const xt = xTicks ?? ticks(x0, x1, 5);
  const uniq = Array.from(new Set(xs)).sort((a, b) => a - b);
  const hx = hover == null ? null : uniq.reduce((a, b) => (Math.abs(b - hover) < Math.abs(a - hover) ? b : a));
  return (
    <div>
      {series.length > 1 && <Legend items={series} />}
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={ariaLabel}
          onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover(x0 + (((e.clientX - r.left) / r.width) * W - m.l) / (W - m.l - m.r) * (x1 - x0)); }}
          onMouseLeave={() => setHover(null)}>
          {ticks(y0, y1).map((t) => (
            <g key={t}>
              <line x1={m.l} x2={W - m.r} y1={sy(t)} y2={sy(t)} stroke={C.grid} strokeWidth="1" />
              <text x={m.l - 6} y={sy(t) + 3.5} textAnchor="end" fontSize="11" fill={C.ink3}>{yFmt(t)}</text>
            </g>
          ))}
          {xt.map((t) => <text key={t} x={sx(t)} y={H - 8} textAnchor="middle" fontSize="11" fill={C.ink3}>{xFmt(t)}</text>)}
          {refLine != null && <line x1={m.l} x2={W - m.r} y1={sy(refLine)} y2={sy(refLine)} stroke={C.ink3} strokeWidth="1" strokeDasharray="4 4" />}
          {series.map((s) => (
            <polyline key={s.label} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeDasharray={s.dashed ? "4 4" : undefined}
              points={s.points.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")} />
          ))}
          {hx != null && (
            <g>
              <line x1={sx(hx)} x2={sx(hx)} y1={m.t} y2={H - m.b} stroke={C.ink3} strokeWidth="1" />
              {series.map((s) => { const p = s.points.find((q) => q.x === hx); return p ? <circle key={s.label} cx={sx(p.x)} cy={sy(p.y)} r="4" fill={s.color} stroke={C.surface} strokeWidth="2" /> : null; })}
            </g>
          )}
        </svg>
        {hx != null && (
          <Tip x={sx(hx)} y={H / 2} w={W}>
            <div className="mb-0.5 text-ink-3">{xFmt(hx)}</div>
            {series.map((s) => { const p = s.points.find((q) => q.x === hx); return p ? (
              <div key={s.label} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: s.color }} />{s.label}: <b className="num">{yFmt(p.y)}</b></div>
            ) : null; })}
          </Tip>
        )}
      </div>
    </div>
  );
}

/** Probability mass function as bars; bars on the winning side of `line` are emphasised. */
export function DistChart({ pmf, line, side, mean, ariaLabel }: { pmf: number[]; line?: number | null; side?: "over" | "under"; mean: number; ariaLabel: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = 200, m = { l: 44, r: 10, t: 10, b: 26 };
  const n = pmf.length, ymax = Math.max(...pmf) * 1.1;
  const bw = (W - m.l - m.r) / n;
  const sy = (v: number) => H - m.b - (v / ymax) * (H - m.t - m.b);
  const win = (k: number) => line == null || !side ? true : side === "over" ? k > line : k < line;
  const step = Math.max(1, Math.ceil(n / 12));
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={ariaLabel} onMouseLeave={() => setHover(null)}>
        {ticks(0, ymax, 3).map((t) => (
          <g key={t}><line x1={m.l} x2={W - m.r} y1={sy(t)} y2={sy(t)} stroke={C.grid} /><text x={m.l - 6} y={sy(t) + 3.5} textAnchor="end" fontSize="11" fill={C.ink3}>{(t * 100).toFixed(0)} %</text></g>
        ))}
        {pmf.map((v, k) => (
          <g key={k} onMouseEnter={() => setHover(k)}>
            <rect x={m.l + k * bw} y={m.t} width={bw} height={H - m.t - m.b} fill="transparent" />
            <rect x={m.l + k * bw + 1} y={sy(v)} width={Math.max(1, bw - 2)} height={Math.max(0, H - m.b - sy(v))} rx={Math.min(3, bw / 3)}
              fill={C.s1} opacity={win(k) ? 1 : 0.35} stroke={hover === k ? C.ink2 : "none"} />
            {k % step === 0 && <text x={m.l + (k + 0.5) * bw} y={H - 8} textAnchor="middle" fontSize="11" fill={C.ink3}>{k}</text>}
          </g>
        ))}
        {line != null && <line x1={m.l + (line + 0.5) * bw} x2={m.l + (line + 0.5) * bw} y1={m.t} y2={H - m.b} stroke={C.ink2} strokeDasharray="4 3" />}
        {line != null && <text x={m.l + (line + 0.5) * bw + 4} y={m.t + 10} fontSize="11" fill={C.ink2}>Linie {line}</text>}
        <line x1={m.l + (mean + 0.5) * bw} x2={m.l + (mean + 0.5) * bw} y1={H - m.b} y2={H - m.b + 5} stroke={C.ink2} strokeWidth="2" />
      </svg>
      {hover != null && <Tip x={m.l + (hover + 0.5) * bw} y={60} w={W}>genau {hover}: <b className="num">{(pmf[hover] * 100).toFixed(1)} %</b><br />≥ {hover}: <b className="num">{(pmf.slice(hover).reduce((a, b) => a + b, 0) * 100).toFixed(1)} %</b></Tip>}
    </div>
  );
}

/** Reliability diagram: predicted vs observed frequency; the diagonal is perfect calibration. */
export function CalibrationChart({ series, ariaLabel }: { series: { label: string; color: string; bins: { predicted: number; observed: number; n: number }[] }[]; ariaLabel: string }) {
  const [hover, setHover] = useState<{ s: number; i: number } | null>(null);
  const W = 420, H = 300, m = { l: 44, r: 12, t: 10, b: 34 };
  const sx = (v: number) => m.l + v * (W - m.l - m.r), sy = (v: number) => H - m.b - v * (H - m.t - m.b);
  const h = hover ? series[hover.s].bins[hover.i] : null;
  return (
    <div>
      <Legend items={[...series, { label: "perfekt kalibriert", color: C.ink3, dashed: true }]} />
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={ariaLabel} onMouseLeave={() => setHover(null)}>
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line x1={m.l} x2={W - m.r} y1={sy(t)} y2={sy(t)} stroke={C.grid} />
              <text x={m.l - 6} y={sy(t) + 3.5} textAnchor="end" fontSize="11" fill={C.ink3}>{t * 100} %</text>
              <text x={sx(t)} y={H - 16} textAnchor="middle" fontSize="11" fill={C.ink3}>{t * 100} %</text>
            </g>
          ))}
          <text x={(W + m.l) / 2} y={H - 2} textAnchor="middle" fontSize="11" fill={C.ink3}>prognostizierte Wahrscheinlichkeit</text>
          <line x1={sx(0)} y1={sy(0)} x2={sx(1)} y2={sy(1)} stroke={C.ink3} strokeDasharray="4 4" />
          {series.map((s, si) => (
            <g key={s.label}>
              <polyline fill="none" stroke={s.color} strokeWidth="2" points={s.bins.map((b) => `${sx(b.predicted)},${sy(b.observed)}`).join(" ")} />
              {s.bins.map((b, i) => (
                <circle key={i} cx={sx(b.predicted)} cy={sy(b.observed)} r={hover?.s === si && hover.i === i ? 6 : 4} fill={s.color} stroke={C.surface} strokeWidth="2"
                  onMouseEnter={() => setHover({ s: si, i })} />
              ))}
            </g>
          ))}
        </svg>
        {h && hover && <Tip x={sx(h.predicted)} y={sy(h.observed)} w={W}>{series[hover.s].label}<br />Prognose <b className="num">{(h.predicted * 100).toFixed(1)} %</b> · beobachtet <b className="num">{(h.observed * 100).toFixed(1)} %</b><br />n = {h.n}</Tip>}
      </div>
    </div>
  );
}

/** Horizontal meter (0..1). */
export function Meter({ value, label }: { value: number; label?: string }) {
  return (
    <div className="flex items-center gap-2" title={label}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-s1" style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} /></div>
      <span className="num text-xs text-ink-2">{Math.round(value * 100)}</span>
    </div>
  );
}
