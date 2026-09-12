import type { ReactNode } from "react";

import type { Tone } from "@/lib/constants";

import "./KpiCard.css";

export interface KpiCardProps {
  label: string;
  value: ReactNode;
  unit?: string;
  /** Formatted delta text (e.g. "+3", "-2.1h"). */
  delta?: string;
  /** Whether the delta is good (green) or bad (red); undefined = neutral. */
  deltaGood?: boolean;
  tone?: Tone;
  hint?: string;
  sparkline?: number[];
  loading?: boolean;
  onClick?: () => void;
  title?: string;
}

function Sparkline({ points, tone }: { points: number[]; tone: Tone }) {
  if (points.length < 2) return null;
  const w = 72;
  const h = 24;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = w / (points.length - 1);
  const d = points.map((p, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - ((p - min) / span) * (h - 2) - 1).toFixed(1)}`).join(" ");
  const stroke = `var(--status-${tone === "neutral" ? "running" : tone})`;
  return (
    <svg className="kpi-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Dense KPI tile: label, mono value, optional delta, hint and sparkline. */
export function KpiCard({
  label,
  value,
  unit,
  delta,
  deltaGood,
  tone = "neutral",
  hint,
  sparkline,
  loading = false,
  onClick,
  title,
}: KpiCardProps) {
  const cls = ["kpi", `kpi-tone-${tone}`, onClick ? "kpi-clickable" : ""].filter(Boolean).join(" ");
  const deltaCls = deltaGood === undefined ? "text-muted" : deltaGood ? "delta-up" : "delta-down";
  const content = (
    <>
      <div className="kpi-label" title={title ?? label}>
        {label}
      </div>
      <div className="kpi-value-row">
        {loading ? <span className="kpi-skeleton" /> : <span className="kpi-value">{value}</span>}
        {unit ? <span className="kpi-unit">{unit}</span> : null}
        {delta ? <span className={`kpi-delta ${deltaCls}`}>{delta}</span> : null}
      </div>
      {hint ? <div className="kpi-hint">{hint}</div> : null}
      {sparkline ? <Sparkline points={sparkline} tone={tone} /> : null}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={cls} onClick={onClick} style={{ textAlign: "left" }}>
        {content}
      </button>
    );
  }
  return <div className={cls}>{content}</div>;
}
