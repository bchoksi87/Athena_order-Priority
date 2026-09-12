import type { PriorityResult } from "@/api/types";
import { ADJUSTMENT_LABELS, READINESS_LABELS } from "@/lib/constants";
import { formatHours, formatSigned } from "@/lib/formatters";
import { formatDateTime } from "@/lib/time";

import { ReadinessPill } from "./StatusPill";
import { RiskBadge } from "./RiskBadge";
import "./ExplanationPanel.css";

export interface ExplanationPanelProps {
  result: PriorityResult;
  /** Hide the big score header (when it is already shown elsewhere). */
  compact?: boolean;
  showRaw?: boolean;
}

function pointsClass(points: number): string {
  if (points > 0.05) return "pos";
  if (points < -0.05) return "neg";
  return "zero";
}

/**
 * "Why?" panel: renders the machine-produced factor and adjustment breakdown
 * as a "+25 — Due date — Due in 18 hours" list. Never invents text: every
 * line comes from the engine's FactorScore / PriorityAdjustment reason.
 */
export function ExplanationPanel({ result, compact = false, showRaw = true }: ExplanationPanelProps) {
  const factors = [...result.factors].sort((a, b) => Math.abs(b.points) - Math.abs(a.points));
  const adjustments = result.adjustments;
  const adjustmentTotal = adjustments.reduce((sum, a) => sum + a.points, 0);

  return (
    <div className="explain" data-testid="explanation-panel">
      {!compact ? (
        <div className="explain-head">
          <div className="explain-score num" data-testid="explanation-score">
            {Math.round(result.score)}
          </div>
          <div className="explain-meta">
            <div className="row">
              <RiskBadge level={result.risk_level} />
              <ReadinessPill state={result.readiness} size="sm" />
              {result.forced_next ? <span className="pill pill-hold pill-sm">FORCED NEXT</span> : null}
              {result.rank !== null ? <span className="num">rank #{result.rank}</span> : null}
            </div>
            <div>
              Profile {result.profile_id} v{result.profile_version} · computed {formatDateTime(result.computed_at)}
            </div>
            <div>
              {result.hours_until_due !== null ? `Due in ${formatHours(result.hours_until_due)}` : "No due date"}
              {result.projected_completion ? ` · projected ${formatDateTime(result.projected_completion)}` : ""}
              {result.projected_lateness_hours !== null && result.projected_lateness_hours > 0
                ? ` · late by ${formatHours(result.projected_lateness_hours)}`
                : ""}
            </div>
          </div>
        </div>
      ) : null}

      {result.blocked && result.blocking_reasons.length > 0 ? (
        <div className="explain-blockers">
          <strong>Blocked — {READINESS_LABELS[result.readiness] ?? result.readiness}</strong>
          <ul>
            {result.blocking_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <h3>Factors</h3>
        <ul className="explain-list" data-testid="explanation-factors">
          {factors.map((f) => (
            <li className="explain-item" key={f.key}>
              <span className={`explain-points ${pointsClass(f.points)}`}>{formatSigned(f.points)}</span>
              <span className="truncate">
                <span className="explain-name">{f.name}</span>
                <span className="explain-reason"> — {f.reason}</span>
              </span>
              {showRaw ? (
                <span className="explain-raw" title="raw score × weight">
                  {Math.round(f.raw_score)} × {(f.weight * 100).toFixed(0)}%{f.kind === "penalty" ? " (pen.)" : ""}
                </span>
              ) : (
                <span />
              )}
            </li>
          ))}
        </ul>
        <div className="explain-total">
          <span>Base score</span>
          <span className="num">{result.base_score.toFixed(1)}</span>
        </div>
      </div>

      {adjustments.length > 0 ? (
        <div>
          <h3>Adjustments</h3>
          <ul className="explain-list" data-testid="explanation-adjustments">
            {adjustments.map((a, i) => (
              <li className="explain-item" key={`${a.kind}-${i}`}>
                <span className={`explain-points ${pointsClass(a.points)}`}>{formatSigned(a.points)}</span>
                <span className="truncate">
                  <span className="explain-name">{ADJUSTMENT_LABELS[a.kind] ?? a.kind}</span>
                  <span className="explain-reason"> — {a.reason}</span>
                </span>
                <span className="explain-raw">{a.source_id ?? ""}</span>
              </li>
            ))}
          </ul>
          <div className="explain-total">
            <span>Adjustments</span>
            <span className="num">{formatSigned(adjustmentTotal, 1)}</span>
          </div>
        </div>
      ) : null}

      <div className="explain-total">
        <span>Total (clamped 0–100)</span>
        <span className="num" data-testid="explanation-total">
          {Math.round(result.score)}
        </span>
      </div>

      {result.explanation ? <div className="explain-text">{result.explanation}</div> : null}
    </div>
  );
}
