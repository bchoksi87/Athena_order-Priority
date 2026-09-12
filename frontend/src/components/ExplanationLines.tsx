import type { ExplanationLine } from "@/api/types";
import { formatPoints, formatSigned } from "@/lib/formatters";

import "./ExplanationPanel.css";

export interface ExplanationLinesProps {
  /** The engine's lines exactly as returned by the API (OrderDetail.breakdown / ExplanationResponse.lines). */
  lines: ExplanationLine[];
  /** Final score (already clamped); rendered as the total line. */
  score: number;
  /** Show raw score × weight next to factor lines. */
  showRaw?: boolean;
  /** Hide the section headings (compact drawer use). */
  compact?: boolean;
}

function pointsClass(points: number): string {
  if (points > 0.05) return "pos";
  if (points < -0.05) return "neg";
  return "zero";
}

function Line({ line, showRaw }: { line: ExplanationLine; showRaw: boolean }) {
  const raw = showRaw && line.kind === "factor" && line.raw_score !== null && line.raw_score !== undefined && line.weight !== null && line.weight !== undefined;
  return (
    <li className="explain-item" data-kind={line.kind} data-key={line.key}>
      <span className={`explain-points ${pointsClass(line.points)}`}>{formatPoints(line.points)}</span>
      <span>
        <span className="explain-name">{line.label}</span>
        <span className="explain-reason">: {line.reason}</span>
      </span>
      {raw ? (
        <span className="explain-raw" title="raw score × weight">
          {Math.round(line.raw_score as number)} × {((line.weight as number) * 100).toFixed(0)}%
        </span>
      ) : line.source_id ? (
        <span className="explain-raw" title="source">
          {line.source_id}
        </span>
      ) : (
        <span />
      )}
    </li>
  );
}

/**
 * "Why is this order prioritised?" — renders the explanation lines produced by the
 * priority engine verbatim (factors → adjustments → caps → total). Never composes text of its own.
 */
export function ExplanationLines({ lines, score, showRaw = true, compact = false }: ExplanationLinesProps) {
  const factors = lines.filter((l) => l.kind === "factor");
  const bonuses = factors.filter((l) => l.points >= 0);
  const penalties = factors.filter((l) => l.points < 0);
  const adjustments = lines.filter((l) => l.kind === "adjustment");
  const caps = lines.filter((l) => l.kind !== "factor" && l.kind !== "adjustment");
  const sum = (xs: ExplanationLine[]) => xs.reduce((s, l) => s + l.points, 0);

  if (lines.length === 0) {
    return (
      <div className="explain" data-testid="explanation-lines">
        <div className="text-muted text-sm">No explanation lines were produced for this order.</div>
      </div>
    );
  }

  return (
    <div className="explain" data-testid="explanation-lines">
      <div>
        {!compact ? <h3>Reasons</h3> : null}
        <ul className="explain-list" data-testid="explanation-bonus-lines">
          {bonuses.map((l, i) => (
            <Line key={`${l.key}-${i}`} line={l} showRaw={showRaw} />
          ))}
        </ul>
      </div>
      {penalties.length > 0 ? (
        <div>
          {!compact ? <h3>Penalties</h3> : null}
          <ul className="explain-list" data-testid="explanation-penalty-lines">
            {penalties.map((l, i) => (
              <Line key={`${l.key}-${i}`} line={l} showRaw={showRaw} />
            ))}
          </ul>
        </div>
      ) : null}
      {factors.length > 0 ? (
        <div className="explain-total explain-subtotal">
          <span>Base score</span>
          <span className="num">{sum(factors).toFixed(1)}</span>
        </div>
      ) : null}
      {adjustments.length > 0 ? (
        <div>
          {!compact ? <h3>Adjustments</h3> : null}
          <ul className="explain-list" data-testid="explanation-adjustment-lines">
            {adjustments.map((l, i) => (
              <Line key={`${l.key}-${i}`} line={l} showRaw={showRaw} />
            ))}
          </ul>
          <div className="explain-total explain-subtotal">
            <span>Adjustments</span>
            <span className="num">{formatSigned(sum(adjustments), 1)}</span>
          </div>
        </div>
      ) : null}
      {caps.length > 0 ? (
        <ul className="explain-list" data-testid="explanation-cap-lines">
          {caps.map((l, i) => (
            <Line key={`${l.key}-${i}`} line={l} showRaw={showRaw} />
          ))}
        </ul>
      ) : null}
      <div className="explain-total">
        <span>Total</span>
        <span className="num" data-testid="explanation-lines-total">
          {Math.round(score)}
        </span>
      </div>
    </div>
  );
}
