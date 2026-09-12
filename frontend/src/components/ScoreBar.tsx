import { useState } from "react";

import { scoreTone } from "@/lib/constants";
import { formatScore, formatSigned } from "@/lib/formatters";

import "./ScoreBar.css";

export interface ScoreBreakdownItem {
  label: string;
  points: number;
}

export interface ScoreBarProps {
  /** Score in [0, 100]. */
  value: number | null | undefined;
  /** Optional factor/adjustment breakdown shown in a hover tooltip. */
  breakdown?: ScoreBreakdownItem[];
  showValue?: boolean;
  width?: number;
  ariaLabel?: string;
}

/** Horizontal 0-100 score bar coloured by urgency, with an optional breakdown tooltip. */
export function ScoreBar({ value, breakdown, showValue = true, width, ariaLabel = "Priority score" }: ScoreBarProps) {
  const [open, setOpen] = useState(false);
  const score = value === null || value === undefined || Number.isNaN(value) ? null : Math.max(0, Math.min(100, value));
  const tone = score === null ? "ready" : scoreTone(score);
  const pct = score ?? 0;
  const hasBreakdown = Boolean(breakdown && breakdown.length > 0);

  return (
    <span
      className="scorebar"
      style={width ? { width } : undefined}
      onMouseEnter={() => hasBreakdown && setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => hasBreakdown && setOpen(true)}
      onBlur={() => setOpen(false)}
      tabIndex={hasBreakdown ? 0 : -1}
      role="meter"
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={score ?? undefined}
      data-testid="scorebar"
    >
      <span className="scorebar-track">
        <span className={`scorebar-fill tone-${tone}`} style={{ width: `${pct}%` }} data-testid="scorebar-fill" />
      </span>
      {showValue ? <span className="scorebar-value">{formatScore(score)}</span> : null}
      {open && breakdown ? (
        <span className="scorebar-tip" role="tooltip">
          {breakdown.map((item, i) => (
            <span className="scorebar-tip-row" key={`${item.label}-${i}`}>
              <span>{item.label}</span>
              <span className={`num ${item.points < 0 ? "tone-late" : "tone-ready"}`}>{formatSigned(item.points)}</span>
            </span>
          ))}
          <span className="scorebar-tip-row scorebar-tip-total">
            <span>Total</span>
            <span className="num">{formatScore(score)}</span>
          </span>
        </span>
      ) : null}
    </span>
  );
}
