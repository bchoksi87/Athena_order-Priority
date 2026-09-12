import type { MetricPair } from "@/api/types";
import { formatMetricDelta, formatMetricValue, metricImproved, orderedMetricPairs } from "@/lib/metricPairs";

import "./ComparisonTable.css";

export interface ComparisonTableProps {
  /** Metric pairs keyed by metric (from ScheduleComparison.metrics or SimulationResponse.comparison). */
  metrics: Record<string, MetricPair>;
  /** Optional quality pair shown first ("Schedule quality: 38 → 34"). */
  quality?: MetricPair | null;
  beforeLabel?: string;
  afterLabel?: string;
  /** Show the signed delta column. */
  showDelta?: boolean;
  dense?: boolean;
}

/** Spec Phase 36 format: "On-time delivery: 87% → 94%" for every metric pair, coloured by improvement. */
export function ComparisonTable({ metrics, quality, beforeLabel = "Before", afterLabel = "After", showDelta = true, dense = false }: ComparisonTableProps) {
  const rows: Array<[string, MetricPair]> = [...(quality ? ([["quality", quality]] as Array<[string, MetricPair]>) : []), ...orderedMetricPairs(metrics)];
  if (rows.length === 0) return <div className="text-muted text-sm">No metrics to compare.</div>;
  return (
    <table className={`cmp${dense ? " cmp-dense" : ""}`} data-testid="comparison-table">
      <thead>
        <tr>
          <th>Metric</th>
          <th className="num">{beforeLabel}</th>
          <th className="cmp-arrow" aria-hidden="true" />
          <th className="num">{afterLabel}</th>
          {showDelta ? <th className="num">Δ</th> : null}
        </tr>
      </thead>
      <tbody>
        {rows.map(([key, pair]) => {
          const improved = metricImproved(key, pair);
          const cls = improved === undefined ? "cmp-same" : improved ? "cmp-better" : "cmp-worse";
          return (
            <tr key={key} className={cls} data-metric={key}>
              <td>{pair.label}</td>
              <td className="num text-muted">{formatMetricValue(key, pair.before)}</td>
              <td className="cmp-arrow">→</td>
              <td className="num strong cmp-after">{formatMetricValue(key, pair.after)}</td>
              {showDelta ? <td className="num cmp-delta">{formatMetricDelta(key, pair.delta ?? (pair.after !== null && pair.before !== null ? pair.after - pair.before : null))}</td> : null}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
