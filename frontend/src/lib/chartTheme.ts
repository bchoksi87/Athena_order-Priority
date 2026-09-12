/** Recharts styling that follows the design tokens (CSS variables resolve inside SVG). */
import type { CSSProperties } from "react";

export const chartColors = {
  primary: "var(--accent)",
  ready: "var(--status-ready)",
  late: "var(--status-late)",
  atRisk: "var(--status-at-risk)",
  blocked: "var(--status-blocked)",
  neutral: "var(--status-neutral)",
  grid: "var(--gantt-grid)",
  axis: "var(--fg-muted)",
} as const;

export const tooltipStyle: CSSProperties = {
  background: "var(--bg-panel-raised)",
  border: "1px solid var(--border-strong)",
  borderRadius: 5,
  fontSize: 12,
  color: "var(--fg)",
  fontFamily: "var(--font-mono)",
};

export const axisTick = { fontSize: 11, fill: "var(--fg-muted)", fontFamily: "var(--font-mono)" } as const;

export function utilizationColor(pct: number, overloadPct = 95, warnPct = 85): string {
  if (pct >= overloadPct) return chartColors.late;
  if (pct >= warnPct) return chartColors.atRisk;
  return chartColors.ready;
}
