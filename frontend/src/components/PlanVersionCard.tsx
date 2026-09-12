import type { ReactNode } from "react";

import type { ScheduleQuality, ScheduleVersionResponse } from "@/api/types";
import { formatHours, formatNumber, formatPct, formatScore } from "@/lib/formatters";
import { formatDateTime } from "@/lib/time";

import { ScheduleStatusPill } from "./StatusPill";
import "./PlanVersionCard.css";

export interface PlanVersionCardProps {
  version: ScheduleVersionResponse | null | undefined;
  /** Full quality block when available (score + summary); falls back to the version's stored score. */
  quality?: ScheduleQuality | null;
  title?: string;
  /** Buttons rendered in the header (generate / approve / publish …). */
  actions?: ReactNode;
  /** Extra content below the facts (e.g. a comparison). */
  children?: ReactNode;
  compact?: boolean;
  loading?: boolean;
}

/**
 * Spec Phase 37 version card: "Schedule v124 · Generated 11 Sep 2026 16:20 · Algorithm PriorityScheduler v2.3 ·
 * Configuration PriorityProfile-A · Status Draft", with the Phase 36 quality score and who approved / published it.
 */
export function PlanVersionCard({ version, quality, title = "Active plan", actions, children, compact = false, loading = false }: PlanVersionCardProps) {
  const score = quality?.score ?? version?.quality_score ?? null;
  const summary = quality?.summary ?? version?.quality_summary ?? null;
  const m = version?.metrics;
  return (
    <div className={`planv${compact ? " planv-compact" : ""}`} data-testid="plan-version-card">
      <div className="planv-head">
        <div className="planv-id">
          <span className="planv-eyebrow">{title}</span>
          {loading ? (
            <span className="planv-version text-muted">…</span>
          ) : version ? (
            <span className="row gap-3">
              <span className="planv-version num">v{version.version_number}</span>
              <ScheduleStatusPill status={version.status} />
              {version.label ? <span className="text-muted text-sm truncate" title={version.label}>{version.label}</span> : null}
            </span>
          ) : (
            <span className="planv-version text-muted">No schedule version</span>
          )}
        </div>
        <div className="planv-quality" title={summary ?? undefined}>
          <span className="planv-eyebrow">Schedule quality</span>
          <span className="num">
            <span className={`planv-score ${score === null ? "text-faint" : score >= 80 ? "tone-ready" : score >= 60 ? "tone-at-risk" : "tone-late"}`}>{formatScore(score)}</span>
            <span className="text-faint">/100</span>
          </span>
        </div>
        {actions ? <div className="planv-actions">{actions}</div> : null}
      </div>
      {summary ? <div className="planv-summary">{summary}</div> : null}
      {version ? (
        <dl className="planv-facts">
          <div>
            <dt>Generated</dt>
            <dd>
              {formatDateTime(version.generated_at)}
              {version.generated_by ? <span className="text-muted"> by {version.generated_by}</span> : null}
            </dd>
          </div>
          <div>
            <dt>Approved</dt>
            <dd>{version.approved_at ? <>{formatDateTime(version.approved_at)}{version.approved_by ? <span className="text-muted"> by {version.approved_by}</span> : null}</> : <span className="text-faint">not yet</span>}</dd>
          </div>
          <div>
            <dt>Published</dt>
            <dd>{version.published_at ? <>{formatDateTime(version.published_at)}{version.published_by ? <span className="text-muted"> by {version.published_by}</span> : null}</> : <span className="text-faint">not yet</span>}</dd>
          </div>
          <div>
            <dt>Algorithm</dt>
            <dd>
              {version.algorithm} <span className="text-muted">v{version.algorithm_version}</span>
            </dd>
          </div>
          <div>
            <dt>Configuration</dt>
            <dd>
              {version.profile_id} <span className="text-muted">v{version.profile_version} · config v{version.config_version}</span>
            </dd>
          </div>
          <div>
            <dt>Horizon</dt>
            <dd className="num">
              {formatDateTime(version.horizon_start, "dd MMM")} – {formatDateTime(version.horizon_end, "dd MMM")}
            </dd>
          </div>
          <div>
            <dt>Entries</dt>
            <dd className="num">
              {formatNumber(version.entry_count)}
              {m ? <span className="text-muted"> · {formatNumber(m.scheduled_orders)} orders · {formatNumber(m.unscheduled_orders)} unscheduled</span> : null}
            </dd>
          </div>
          {m ? (
            <div>
              <dt>Outcome</dt>
              <dd className="num">
                On-time {formatPct(m.on_time_pct, 0)} · late {formatNumber(m.late_orders)} · avg lateness {formatHours(m.avg_lateness_hours)} · util {formatPct(m.overall_utilization_pct, 0)}
              </dd>
            </div>
          ) : null}
          {version.trigger || version.previous_version !== null ? (
            <div>
              <dt>Origin</dt>
              <dd>
                {version.trigger ?? "manual"}
                {version.previous_version !== null ? <span className="text-muted"> · from v{version.previous_version}</span> : null}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : loading ? null : (
        <div className="text-muted text-sm">Generate a schedule to create the first version.</div>
      )}
      {children}
    </div>
  );
}
