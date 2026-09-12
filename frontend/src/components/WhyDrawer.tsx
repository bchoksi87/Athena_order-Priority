import { useOrderExplanation } from "@/api/orders";
import { isApiError } from "@/api/client";
import { READINESS_LABELS } from "@/lib/constants";
import { formatHours } from "@/lib/formatters";
import { formatDateTime } from "@/lib/time";

import { Drawer } from "./Drawer";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { ExplanationLines } from "./ExplanationLines";
import { LoadingState } from "./LoadingState";
import { RiskBadge } from "./RiskBadge";
import { ReadinessPill } from "./StatusPill";
import "./ExplanationPanel.css";

export interface WhyPanelProps {
  orderId: string;
  now?: Date;
}

/** "Why is this order prioritised?" — the engine's explanation lines for one order (GET /orders/{id}/explanation). */
export function WhyPanel({ orderId, now = new Date() }: WhyPanelProps) {
  const explanation = useOrderExplanation(orderId);
  if (explanation.isPending) return <LoadingState compact label="Loading explanation" />;
  if (explanation.isError) {
    const notScored = isApiError(explanation.error) && explanation.error.isNotFound;
    return notScored ? (
      <EmptyState compact title="Not scored yet" message="No priority result is stored for this order. Generate a schedule to compute scores and explanations." />
    ) : (
      <ErrorState compact error={explanation.error} onRetry={() => void explanation.refetch()} />
    );
  }
  const x = explanation.data;
  return (
    <div className="col gap-3" data-testid="why-panel">
      <div className="row row-wrap gap-3">
        <div className="explain-score num" data-testid="why-score">
          {Math.round(x.score)}
        </div>
        <div className="col gap-1 text-xs text-muted">
          <div className="row">
            <RiskBadge level={x.risk_level} />
            <ReadinessPill state={x.readiness} size="sm" />
            {x.forced_next ? <span className="pill pill-hold pill-sm">FORCED NEXT</span> : null}
            {x.rank !== null ? <span className="num">rank #{x.rank}</span> : null}
          </div>
          <div>
            Profile {x.profile_id} v{x.profile_version} · computed {formatDateTime(x.computed_at, "dd MMM HH:mm")} ({formatHours((now.getTime() - new Date(x.computed_at).getTime()) / 3_600_000, 1)} ago)
          </div>
        </div>
      </div>
      {x.blocked ? (
        <div className="explain-blockers">
          <strong>Blocked — {READINESS_LABELS[x.readiness] ?? x.readiness}</strong>
          {x.blocking_reasons.length > 0 ? (
            <ul>
              {x.blocking_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      <ExplanationLines lines={x.lines} score={x.score} />
    </div>
  );
}

export interface WhyDrawerProps {
  orderId: string | null;
  onClose: () => void;
  onOpenOrder?: (orderId: string) => void;
  /** Extra header controls (e.g. quick actions). */
  actions?: React.ReactNode;
  /** Extra content rendered above the explanation (e.g. order facts). */
  children?: React.ReactNode;
  now?: Date;
}

/** Right-hand "Why?" slide-over reused by the control tower and the boards. */
export function WhyDrawer({ orderId, onClose, onOpenOrder, actions, children, now }: WhyDrawerProps) {
  return (
    <Drawer
      open={orderId !== null}
      title={
        <>
          <span className="text-muted">Why is</span>
          <span className="mono">{orderId}</span>
          <span className="text-muted">prioritised?</span>
        </>
      }
      onClose={onClose}
      actions={
        orderId && onOpenOrder ? (
          <>
            {actions}
            <button type="button" className="btn btn-sm btn-primary" onClick={() => onOpenOrder(orderId)}>
              Open order
            </button>
          </>
        ) : (
          actions
        )
      }
    >
      {orderId ? (
        <>
          {children}
          <WhyPanel orderId={orderId} now={now} />
        </>
      ) : null}
    </Drawer>
  );
}
