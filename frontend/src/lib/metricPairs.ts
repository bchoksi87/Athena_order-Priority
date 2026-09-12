/**
 * Presentation of the backend's MetricPair comparisons ("On-time delivery: 87% → 94%").
 * The metric key decides the unit and whether a higher value is good; the label comes from the API.
 */
import type { MetricPair } from "@/api/types";

import { DASH, formatCurrency, formatNumber, formatPct, formatScore, formatSigned } from "./formatters";

export type MetricUnit = "pct" | "hours" | "currency" | "count" | "score";

/** Metrics where a lower value is better (lateness, setup, late orders, money at risk). */
const LOWER_IS_BETTER = new Set([
  "avg_lateness_hours",
  "avg_lateness",
  "max_lateness_hours",
  "total_tardiness_hours",
  "total_setup_hours",
  "setup_hours",
  "setup_count",
  "late_orders",
  "orders_at_risk",
  "unscheduled_orders",
  "revenue_at_risk",
  "margin_at_risk",
  "additional_overtime_hours",
  "makespan_hours",
  "wip_orders_avg",
]);

export function metricUnit(key: string): MetricUnit {
  if (key === "quality" || key === "quality_score" || key === "schedule_quality") return "score";
  if (key.endsWith("_pct") || key.includes("pct") || key.includes("utilization") || key.includes("on_time")) return "pct";
  if (key.includes("revenue") || key.includes("margin")) return "currency";
  if (key.includes("hours") || key.includes("lateness") || key.includes("makespan")) return "hours";
  return "count";
}

export function higherIsBetter(key: string): boolean {
  return !LOWER_IS_BETTER.has(key);
}

/** Hours stay hours ("126h", never "5.3d") so setup / lateness totals read like the spec. */
function formatPlainHours(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const abs = Math.abs(value);
  const text = abs >= 100 ? abs.toFixed(0) : abs.toFixed(1);
  return `${value < 0 ? "-" : ""}${text}h`;
}

export function formatMetricValue(key: string, value: number | null | undefined): string {
  switch (metricUnit(key)) {
    case "pct":
      return formatPct(value, 0);
    case "hours":
      return formatPlainHours(value);
    case "currency":
      return formatCurrency(value);
    case "score":
      return formatScore(value);
    default:
      return formatNumber(value);
  }
}

export function formatMetricDelta(key: string, delta: number | null | undefined): string {
  if (delta === null || delta === undefined || Number.isNaN(delta)) return "";
  switch (metricUnit(key)) {
    case "pct":
      return `${formatSigned(delta, 1)} pt`;
    case "hours":
      return `${formatSigned(delta, 1)} h`;
    case "currency":
      return `${delta > 0 ? "+" : delta < 0 ? "-" : ""}${formatCurrency(Math.abs(delta))}`;
    case "score":
      return formatSigned(delta, 1);
    default:
      return formatSigned(delta, 0);
  }
}

/** true = improvement, false = regression, undefined = unchanged / unknown. */
export function metricImproved(key: string, pair: MetricPair): boolean | undefined {
  const delta = pair.delta ?? (pair.after !== null && pair.before !== null ? pair.after - pair.before : null);
  if (delta === null || Math.abs(delta) < 1e-9) return undefined;
  return higherIsBetter(key) ? delta > 0 : delta < 0;
}

/** "87% → 94%" for one pair. */
export function formatPairTransition(key: string, pair: MetricPair): string {
  return `${formatMetricValue(key, pair.before)} → ${formatMetricValue(key, pair.after)}`;
}

/** The spec's headline order (Phase 36) first, then everything else the API returned. */
const HEADLINE_ORDER = ["on_time_pct", "avg_lateness_hours", "overall_utilization_pct", "total_setup_hours", "late_orders", "orders_at_risk", "revenue_at_risk", "margin_at_risk", "scheduled_orders"];

export function orderedMetricPairs(metrics: Record<string, MetricPair>): Array<[string, MetricPair]> {
  const entries = Object.entries(metrics);
  const rank = (k: string) => {
    const i = HEADLINE_ORDER.indexOf(k);
    return i === -1 ? HEADLINE_ORDER.length : i;
  };
  return entries.sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
}
