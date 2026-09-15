/**
 * Plan bar of the control tower: the active version with its quality score and the audited
 * workflow actions (generate → approve → publish, reject) plus "Evaluate replan" whose decision
 * and old-vs-new comparison are rendered underneath (spec Phases 11, 36, 37).
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { describeError } from "@/api/client";
import { useApproveSchedule, useEvaluateReplan, useGenerateSchedule, usePublishSchedule, useRejectSchedule, useScheduleVersions } from "@/api/schedule";
import type { ReplanOutcome, ScheduleQualityReport, ScheduleVersionResponse, SchedulePlan } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { ComparisonTable } from "@/components/ComparisonTable";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PlanVersionCard } from "@/components/PlanVersionCard";
import { Section } from "@/components/Section";
import { ScheduleStatusPill, StatusPill } from "@/components/StatusPill";
import { useToast } from "@/components/Toast";
import { humanize, type Tone } from "@/lib/constants";
import { formatNumber, formatPct } from "@/lib/formatters";
import { formatDateTime } from "@/lib/time";

type WorkflowAction = "approve" | "publish" | "reject";

const ACTION_LABEL: Record<WorkflowAction, string> = { approve: "Approve", publish: "Publish", reject: "Reject" };
const ACTION_HELP: Record<WorkflowAction, string> = {
  approve: "DRAFT → APPROVED. The version becomes the plan the shop floor may be released against; publishing still requires a second step.",
  publish: "APPROVED → PUBLISHED. Goes through the ERP writeback gateway (in READ ONLY mode a skipped receipt is recorded); older versions are superseded.",
  reject: "The version is marked REJECTED and stays in the history for traceability.",
};

const REPLAN_TONE: Record<string, Tone> = { not_triggered: "neutral", rejected: "late", awaiting_approval: "at-risk", approved: "ready", published: "ready" };

export interface PlanBarProps {
  plan: SchedulePlan | undefined;
  quality: ScheduleQualityReport | undefined;
  loading: boolean;
}

export function PlanBar({ plan, quality, loading }: PlanBarProps) {
  const navigate = useNavigate();
  const toast = useToast();
  const { hasMinRole } = useAuth();
  const generate = useGenerateSchedule();
  const approve = useApproveSchedule();
  const publish = usePublishSchedule();
  const reject = useRejectSchedule();
  const replan = useEvaluateReplan();
  const [pending, setPending] = useState<{ action: WorkflowAction; version: ScheduleVersionResponse } | null>(null);
  const [replanOutcome, setReplanOutcome] = useState<ReplanOutcome | null>(null);

  const active = plan?.version ?? null;
  // A newer draft than the active plan awaits the manager's decision (the report exposes it).
  const draft = quality?.newest_draft && (!active || quality.newest_draft.version_number !== active.version_number) ? quality.newest_draft : null;
  // An approved version that is not the active plan can still be published (the API allows it);
  // without this the manager could approve a newer draft but never release it while a plan is live.
  const approvedVersions = useScheduleVersions({ status: "approved", page_size: 5 });
  const approvedCandidate =
    approvedVersions.data?.items.find((v) => !active || v.version_number !== active.version_number) ?? null;
  const canPlan = hasMinRole("planner");
  const canApprove = hasMinRole("production_manager");

  const run = async (label: string, fn: () => Promise<unknown>, success?: (r: unknown) => string | undefined) => {
    try {
      const r = await fn();
      toast.push({ tone: "success", title: label, message: success?.(r) });
    } catch (err) {
      toast.push({ tone: "error", title: `${label} failed`, message: describeError(err) });
    }
  };

  const submitWorkflow = async (reason: string) => {
    if (!pending) return;
    const body = { version: pending.version.version_number, reason };
    const label = `${ACTION_LABEL[pending.action]} v${pending.version.version_number}`;
    setPending(null);
    if (pending.action === "approve") await run(label, () => approve.mutateAsync(body));
    else if (pending.action === "publish")
      await run(label, () => publish.mutateAsync(body), (r) => {
        const p = r as { receipt: { mode: string; status: string; message: string }; superseded: number };
        return `Writeback ${p.receipt.mode}: ${p.receipt.status}${p.receipt.message ? ` — ${p.receipt.message}` : ""} · ${p.superseded} superseded`;
      });
    else await run(label, () => reject.mutateAsync(body));
  };

  const evaluate = async () => {
    try {
      const outcome = await replan.mutateAsync({ trigger: "manual", reason: "Evaluated from the control tower" });
      setReplanOutcome(outcome);
      toast.push({ tone: outcome.triggered ? "success" : "info", title: `Replan ${humanize(outcome.action)}`, message: outcome.reason });
    } catch (err) {
      toast.push({ tone: "error", title: "Replan evaluation failed", message: describeError(err) });
    }
  };

  const busy = generate.isPending || approve.isPending || publish.isPending || reject.isPending || replan.isPending;

  const actions = (
    <>
      {canPlan ? (
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={busy}
          onClick={() => void run("Schedule generated", () => generate.mutateAsync({ note: "Generated from the control tower" }), (r) => {
            const g = r as { version: { version_number: number }; entries: number; unscheduled: number };
            return `Draft v${g.version.version_number}: ${formatNumber(g.entries)} entries, ${formatNumber(g.unscheduled)} unscheduled`;
          })}
        >
          {generate.isPending ? "Generating…" : "Generate schedule"}
        </button>
      ) : null}
      {canApprove && active?.status === "draft" ? (
        <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setPending({ action: "approve", version: active })}>
          Approve v{active.version_number}
        </button>
      ) : null}
      {canApprove && active?.status === "approved" ? (
        <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setPending({ action: "publish", version: active })}>
          Publish v{active.version_number}
        </button>
      ) : null}
      {canApprove && approvedCandidate && active?.status !== "approved" ? (
        <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setPending({ action: "publish", version: approvedCandidate })}>
          Publish v{approvedCandidate.version_number}
        </button>
      ) : null}
      {canApprove && active && (active.status === "draft" || active.status === "approved") ? (
        <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => setPending({ action: "reject", version: active })}>
          Reject v{active.version_number}
        </button>
      ) : null}
      {canPlan ? (
        <button type="button" className="btn btn-sm" disabled={busy || !active} onClick={() => void evaluate()} title="Detect changes since the active plan, generate a candidate and apply the stability rules">
          {replan.isPending ? "Evaluating…" : "Evaluate replan"}
        </button>
      ) : null}
      <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(routes.gantt)}>
        Gantt
      </button>
      <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(routes.machineSchedule)}>
        Machines
      </button>
    </>
  );

  return (
    <div className="col gap-3" data-testid="plan-bar">
      <PlanVersionCard version={active} quality={quality?.version && active && quality.version.version_number === active.version_number ? quality.quality : null} title={plan ? `Active plan · ${humanize(plan.status)}` : "Active plan"} actions={actions} loading={loading} compact>
        {draft ? (
          <div className="col gap-2" data-testid="draft-pending">
            <div className="row row-wrap gap-3">
              <span className="text-sm">
                Newer draft <span className="mono strong">v{draft.version_number}</span> <ScheduleStatusPill status={draft.status} size="sm" /> generated {formatDateTime(draft.generated_at)}
                {draft.trigger ? <span className="text-muted"> · {draft.trigger}</span> : null} · quality {draft.quality_score === null ? "—" : Math.round(draft.quality_score)}/100
              </span>
              {canApprove ? (
                <span className="row gap-1">
                  {draft.status === "approved" ? (
                    <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setPending({ action: "publish", version: draft })}>
                      Publish v{draft.version_number}
                    </button>
                  ) : (
                    <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setPending({ action: "approve", version: draft })}>
                      Approve v{draft.version_number}
                    </button>
                  )}
                  <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => setPending({ action: "reject", version: draft })}>
                    Reject v{draft.version_number}
                  </button>
                </span>
              ) : null}
            </div>
            {quality?.comparison ? (
              <ComparisonTable metrics={quality.comparison.metrics} quality={quality.comparison.quality} beforeLabel={`v${quality.comparison.a.version_number}`} afterLabel={`v${quality.comparison.b.version_number}`} dense />
            ) : null}
          </div>
        ) : null}
      </PlanVersionCard>

      {replanOutcome ? (
        <Section
          title="Replan evaluation"
          count={formatDateTime(replanOutcome.evaluated_at)}
          actions={
            <>
              <StatusPill tone={REPLAN_TONE[replanOutcome.action] ?? "neutral"}>{humanize(replanOutcome.action)}</StatusPill>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setReplanOutcome(null)}>
                Dismiss
              </button>
            </>
          }
        >
          <div className="col gap-3" data-testid="replan-result">
            <div className="text-sm">{replanOutcome.reason}</div>
            <dl className="ct-replan-decision">
              <div>
                <dt>Trigger</dt>
                <dd>{humanize(replanOutcome.trigger)}</dd>
              </div>
              <div>
                <dt>Events detected</dt>
                <dd className="num">
                  {replanOutcome.events.length}
                  {replanOutcome.event_types.length ? <span className="text-muted"> · {replanOutcome.event_types.map(humanize).join(", ")}</span> : null}
                </dd>
              </div>
              {replanOutcome.decision ? (
                <>
                  <div>
                    <dt>Should replan</dt>
                    <dd>{replanOutcome.decision.should_replan ? "yes" : "no"}</dd>
                  </div>
                  <div>
                    <dt>Improvement</dt>
                    <dd className="num">{formatPct(replanOutcome.decision.improvement_pct, 1)}</dd>
                  </div>
                  <div>
                    <dt>Changed entries</dt>
                    <dd className="num">{formatNumber(replanOutcome.decision.changed_entries)}</dd>
                  </div>
                  <div>
                    <dt>Frozen-window violations</dt>
                    <dd className={`num ${replanOutcome.decision.frozen_violations > 0 ? "tone-late" : ""}`}>{formatNumber(replanOutcome.decision.frozen_violations)}</dd>
                  </div>
                  <div>
                    <dt>Requires approval</dt>
                    <dd>{replanOutcome.decision.requires_approval ? "yes" : "no"}</dd>
                  </div>
                </>
              ) : null}
              {replanOutcome.candidate_version ? (
                <div>
                  <dt>Candidate</dt>
                  <dd>
                    v{replanOutcome.candidate_version.version_number} <ScheduleStatusPill status={replanOutcome.candidate_version.status} size="sm" />
                  </dd>
                </div>
              ) : null}
            </dl>
            {replanOutcome.comparison ? (
              <div className="col gap-1">
                <div className="text-xs text-muted upper">
                  Old vs new plan · v{replanOutcome.comparison.a.version_number} → v{replanOutcome.comparison.b.version_number} · {replanOutcome.comparison.moved_orders} orders moved
                </div>
                <ComparisonTable metrics={replanOutcome.comparison.metrics} quality={replanOutcome.comparison.quality} beforeLabel={`v${replanOutcome.comparison.a.version_number}`} afterLabel={`v${replanOutcome.comparison.b.version_number}`} />
                <div className="text-xs text-faint">{replanOutcome.comparison.summary}</div>
              </div>
            ) : null}
            {replanOutcome.events.length > 0 ? (
              <ul className="reason-list text-sm text-muted">
                {replanOutcome.events.slice(0, 8).map((e, i) => (
                  <li key={i}>
                    <span className="mono">{humanize(e.type)}</span> · {e.message}
                  </li>
                ))}
                {replanOutcome.events.length > 8 ? <li>… {replanOutcome.events.length - 8} more</li> : null}
              </ul>
            ) : null}
            {replanOutcome.candidate_version && replanOutcome.action === "awaiting_approval" && canApprove ? (
              <div className="row">
                <button type="button" className="btn btn-sm btn-primary" onClick={() => setPending({ action: "approve", version: replanOutcome.candidate_version as ScheduleVersionResponse })}>
                  Approve candidate v{replanOutcome.candidate_version.version_number}
                </button>
                <button type="button" className="btn btn-sm btn-danger" onClick={() => setPending({ action: "reject", version: replanOutcome.candidate_version as ScheduleVersionResponse })}>
                  Reject candidate
                </button>
              </div>
            ) : null}
          </div>
        </Section>
      ) : null}

      {pending ? (
        <ConfirmDialog
          open
          title={`${ACTION_LABEL[pending.action]} schedule v${pending.version.version_number}`}
          message={
            <div className="col gap-2 text-sm">
              <div>{ACTION_HELP[pending.action]}</div>
              <div className="text-muted">
                v{pending.version.version_number} · {humanize(pending.version.status)} · generated {formatDateTime(pending.version.generated_at)} · quality {pending.version.quality_score === null ? "—" : Math.round(pending.version.quality_score)}/100
              </div>
            </div>
          }
          confirmLabel={ACTION_LABEL[pending.action]}
          danger={pending.action === "reject"}
          requireReason
          busy={busy}
          onConfirm={(reason) => void submitWorkflow(reason)}
          onCancel={() => setPending(null)}
        />
      ) : null}
    </div>
  );
}
