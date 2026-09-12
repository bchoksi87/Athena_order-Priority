import type { Bottleneck } from "@/api/types";
import { humanize } from "@/lib/constants";
import { formatCurrency, formatNumber, formatPct } from "@/lib/formatters";
import { formatWorkHours } from "@/lib/metricPairs";

import { RiskBadge } from "./RiskBadge";
import "./BottleneckCard.css";

export interface BottleneckCardProps {
  bottleneck: Bottleneck | null | undefined;
  /** Eyebrow text, default "Current bottleneck". */
  title?: string;
  onOpen?: (b: Bottleneck) => void;
  openLabel?: string;
  compact?: boolean;
}

/**
 * Spec Phase 12 hero block, verbatim format:
 * "Current bottleneck: CNC 5-axis machining · Utilization 96% · Orders waiting 73 · Capacity shortfall 41 machine hours · Revenue at risk ₹XX".
 */
export function BottleneckCard({ bottleneck, title = "Current bottleneck", onOpen, openLabel = "Affected orders", compact = false }: BottleneckCardProps) {
  if (!bottleneck) {
    return (
      <div className={`bneck bneck-none${compact ? " bneck-compact" : ""}`} data-testid="bottleneck-card">
        <div className="bneck-eyebrow">{title}</div>
        <div className="bneck-name">No bottleneck detected</div>
        <div className="text-muted text-sm">Every resource is within its capacity for the horizon.</div>
      </div>
    );
  }
  const b = bottleneck;
  return (
    <div className={`bneck bneck-${b.severity}${compact ? " bneck-compact" : ""}`} data-testid="bottleneck-card">
      <div className="bneck-head">
        <div>
          <div className="bneck-eyebrow">
            {title}
            <span className="text-faint"> · {humanize(b.resource_type)}</span>
          </div>
          <div className="bneck-name" title={b.resource_id}>
            {b.resource_name}
          </div>
        </div>
        <RiskBadge level={b.severity} />
      </div>
      <dl className="bneck-facts">
        <div>
          <dt>Utilization</dt>
          <dd className={`num ${b.utilization_pct >= 100 ? "tone-late" : b.utilization_pct >= 85 ? "tone-at-risk" : ""}`}>{formatPct(b.utilization_pct, 0)}</dd>
        </div>
        <div>
          <dt>Orders waiting</dt>
          <dd className="num">{formatNumber(b.orders_waiting)}</dd>
        </div>
        <div>
          <dt>Shortfall (machine hours)</dt>
          <dd className="num">{formatWorkHours(b.capacity_shortfall_hours, 0)}</dd>
        </div>
        <div>
          <dt>Revenue at risk</dt>
          <dd className="num tone-late">{formatCurrency(b.revenue_at_risk)}</dd>
        </div>
        <div>
          <dt>Margin at risk</dt>
          <dd className="num">{formatCurrency(b.margin_at_risk)}</dd>
        </div>
      </dl>
      <div className="bneck-rec">
        <span className="bneck-rec-label">Recommendation</span>
        <span>{b.recommendation}</span>
      </div>
      {onOpen ? (
        <div className="row">
          <button type="button" className="btn btn-sm" onClick={() => onOpen(b)}>
            {openLabel}
          </button>
        </div>
      ) : null}
    </div>
  );
}
