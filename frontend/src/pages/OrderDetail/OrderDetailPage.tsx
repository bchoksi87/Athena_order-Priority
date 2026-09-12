/**
 * Order Detail (spec Phase 8 ORDER DETAIL VIEW + Phase 34 explainability): every fact about one
 * order, the engine's "Why is this order prioritised?" lines verbatim, machine options, the
 * schedule placement, active overrides/expedites/locks and the audit trail.
 */
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { describeError, isApiError } from "@/api/client";
import { useOrder, useOrderMachines } from "@/api/orders";
import { useCancelOverlay, type OverlayCancelInput } from "@/api/overrides";
import type { AuditEntry, Expedite, MachineOption, OperationResponse, PriorityOverride, ScheduleEntry, ScheduleLock } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { ActionDialog } from "@/components/ActionDialog";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ExplanationLines } from "@/components/ExplanationLines";
import { JsonDiff } from "@/components/JsonDiff";
import { KpiCard } from "@/components/KpiCard";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { DataQualitySeverityBadge, RiskBadge } from "@/components/RiskBadge";
import { ScoreBar } from "@/components/ScoreBar";
import { Section } from "@/components/Section";
import { OrderStatusPill, ReadinessPill, StatusPill } from "@/components/StatusPill";
import { useToast } from "@/components/Toast";
import { LOCK_TYPE_LABELS, OVERRIDE_TYPE_LABELS, OVERRIDE_TYPE_TONE, READINESS_LABELS, humanize } from "@/lib/constants";
import { formatCurrency, formatHours, formatMinutes, formatNumber, formatPct, formatScore } from "@/lib/formatters";
import { formatDateTime, formatRelative, hoursUntil } from "@/lib/time";

import type { OrderActionKind } from "../shared/OrderActionDialog";
import { OrderActions } from "./OrderActions";

const OP_TONE: Record<OperationResponse["operation_status"], "done" | "running" | "hold" | "neutral" | "ready" | "at-risk" | "late"> = {
  pending: "neutral",
  ready: "ready",
  scheduled: "running",
  in_progress: "running",
  completed: "done",
  on_hold: "hold",
  rework: "at-risk",
  cancelled: "neutral",
};

const operationColumns: Column<OperationResponse>[] = [
  { key: "seq", header: "Seq", width: 48, numeric: true, cell: (o) => o.sequence, sortValue: (o) => o.sequence },
  { key: "type", header: "Operation", cell: (o) => <span className="strong">{humanize(o.operation_type)}</span>, sortValue: (o) => o.operation_type },
  { key: "status", header: "Status", cell: (o) => <StatusPill tone={OP_TONE[o.operation_status]} size="sm">{humanize(o.operation_status)}</StatusPill>, sortValue: (o) => o.operation_status },
  { key: "machine", header: "Machine / group", cell: (o) => (o.machine_id ? <Link to={routes.machineDetail(encodeURIComponent(o.machine_id))} className="mono" onClick={(e) => e.stopPropagation()}>{o.machine_id}</Link> : <span className="mono text-muted">{o.machine_group ?? "—"}</span>) },
  { key: "setup", header: "Setup", numeric: true, cell: (o) => (o.setup_minutes === null ? <span className="tone-blocked">missing</span> : formatMinutes(o.setup_minutes)) },
  { key: "cycle", header: "Cycle/unit", numeric: true, cell: (o) => (o.cycle_minutes_per_unit === null ? <span className="tone-blocked">missing</span> : `${formatNumber(o.cycle_minutes_per_unit, 2)} min`) },
  { key: "qty", header: "Done / qty", numeric: true, cell: (o) => `${formatNumber(o.completed_quantity)} / ${formatNumber(o.quantity)}` },
  { key: "material", header: "Material", cell: (o) => <span className="mono">{o.material_id ?? "—"}</span> },
  { key: "tooling", header: "Tooling", cell: (o) => <span className="mono text-muted">{o.tooling_ids.length ? o.tooling_ids.join(", ") : "—"}</span> },
  { key: "prereq", header: "After", cell: (o) => <span className="mono text-faint">{o.prerequisite_operation_id ?? "—"}</span> },
  { key: "est", header: "Estimated", cell: (o) => <span className="num text-nowrap">{formatDateTime(o.estimated_start, "dd MMM HH:mm")} → {formatDateTime(o.estimated_end, "dd MMM HH:mm")}</span>, sortValue: (o) => o.estimated_start ?? "" },
  { key: "actual", header: "Actual", cell: (o) => <span className="num text-nowrap">{o.actual_start ? `${formatDateTime(o.actual_start, "dd MMM HH:mm")} → ${formatDateTime(o.actual_end, "dd MMM HH:mm")}` : "—"}</span>, sortValue: (o) => o.actual_start ?? "" },
];

const entryColumns: Column<ScheduleEntry>[] = [
  { key: "machine", header: "Machine", cell: (e) => <Link to={routes.machineDetail(encodeURIComponent(e.machine_id))} className="mono strong" onClick={(ev) => ev.stopPropagation()}>{e.machine_id}</Link> },
  { key: "op", header: "Operation", cell: (e) => <span className="mono">{e.operation_id}</span> },
  { key: "seq", header: "Seq on machine", numeric: true, cell: (e) => e.sequence_on_machine },
  { key: "setup", header: "Setup start", cell: (e) => <span className="num">{formatDateTime(e.setup_start, "dd MMM HH:mm")}</span> },
  { key: "start", header: "Start", cell: (e) => <span className="num">{formatDateTime(e.start, "dd MMM HH:mm")}</span>, sortValue: (e) => e.start },
  { key: "end", header: "End", cell: (e) => <span className="num">{formatDateTime(e.end, "dd MMM HH:mm")}</span> },
  { key: "dur", header: "Setup + run", numeric: true, cell: (e) => `${formatMinutes(e.setup_minutes)} + ${formatMinutes(e.run_minutes)}` },
  { key: "late", header: "Lateness", numeric: true, cell: (e) => (e.expected_lateness_hours !== null && e.expected_lateness_hours > 0 ? <span className="tone-late strong">+{formatHours(e.expected_lateness_hours)}</span> : <span className="tone-ready">on time</span>) },
  { key: "lock", header: "Locked", width: 60, cell: (e) => (e.locked ? <span className="pill pill-hold pill-sm">LOCKED</span> : "") },
  { key: "why", header: "Placement reason", cell: (e) => <span className="text-muted">{e.placement_reason}</span> },
];

function candidateColumns(onPick: ((m: MachineOption, action: OrderActionKind) => void) | null): Column<MachineOption>[] {
  const cols: Column<MachineOption>[] = [
    { key: "rank", header: "Rank", width: 50, numeric: true, cell: (c) => c.rank, sortValue: (c) => c.rank },
    {
      key: "machine",
      header: "Machine",
      cell: (c) => (
        <span className="text-nowrap">
          <Link to={routes.machineDetail(encodeURIComponent(c.machine_id))} className="mono strong" onClick={(e) => e.stopPropagation()}>
            {c.machine_id}
          </Link>
          <span className="text-muted"> {c.machine_name}</span>
          {c.recommended ? <span className="pill pill-ready pill-sm" style={{ marginLeft: 6 }}>RECOMMENDED</span> : null}
        </span>
      ),
    },
    { key: "setup", header: "Setup", numeric: true, cell: (c) => formatMinutes(c.setup_minutes), sortValue: (c) => c.setup_minutes ?? -1 },
    { key: "run", header: "Run", numeric: true, cell: (c) => formatMinutes(c.run_minutes), sortValue: (c) => c.run_minutes ?? -1 },
    { key: "cost", header: "Soft cost", numeric: true, cell: (c) => formatNumber(c.soft_cost, 1), sortValue: (c) => c.soft_cost, title: "Setup + preference + balance costs used to rank machines" },
    { key: "reasons", header: "Reasons", cell: (c) => (c.reasons.length ? <ul className="reason-list">{c.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul> : <span className="text-faint">—</span>) },
  ];
  if (onPick) {
    cols.push({
      key: "act",
      header: "",
      align: "right",
      cell: (c) => (
        <span className="row gap-1" style={{ justifyContent: "flex-end" }} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <button type="button" className="btn btn-sm" onClick={() => onPick(c, "move")}>
            Move here
          </button>
          <button type="button" className="btn btn-sm" onClick={() => onPick(c, "lock-machine")}>
            Lock
          </button>
        </span>
      ),
    });
  }
  return cols;
}

function OverlayTables({ overrides, expedites, locks, onCancel, canCancel }: { overrides: PriorityOverride[]; expedites: Expedite[]; locks: ScheduleLock[]; onCancel: (input: Omit<OverlayCancelInput, "reason"> & { label: string }) => void; canCancel: boolean }) {
  const overrideColumns: Column<PriorityOverride>[] = [
    { key: "type", header: "Override", cell: (o) => <StatusPill tone={OVERRIDE_TYPE_TONE[o.override_type]} size="sm">{OVERRIDE_TYPE_LABELS[o.override_type]}</StatusPill> },
    { key: "value", header: "Value", numeric: true, cell: (o) => (o.value === null ? "—" : formatNumber(o.value, 1)) },
    { key: "target", header: "Machine", cell: (o) => <span className="mono">{o.target_machine_id ?? "—"}</span> },
    { key: "by", header: "By", cell: (o) => o.created_by },
    { key: "at", header: "At", cell: (o) => <span className="num">{formatDateTime(o.created_at)}</span>, sortValue: (o) => o.created_at },
    { key: "exp", header: "Expires", cell: (o) => <span className="num">{o.expires_at ? formatDateTime(o.expires_at) : "never"}</span> },
    { key: "active", header: "State", cell: (o) => (o.active ? <StatusPill tone="ready" size="sm">active</StatusPill> : <StatusPill tone="done" size="sm">ended</StatusPill>) },
    { key: "reason", header: "Reason", cell: (o) => o.reason },
    { key: "act", header: "", align: "right", cell: (o) => (canCancel && o.active ? <button type="button" className="btn btn-sm btn-ghost" onClick={() => onCancel({ kind: "override", id: o.override_id, label: `${OVERRIDE_TYPE_LABELS[o.override_type]} (${o.override_id})` })}>Cancel</button> : null) },
  ];
  const expediteColumns: Column<Expedite>[] = [
    { key: "points", header: "Boost", numeric: true, cell: (e) => <span className="tone-ready strong">+{formatNumber(e.boost_points, 0)}</span> },
    { key: "by", header: "By", cell: (e) => e.created_by },
    { key: "window", header: "Window", cell: (e) => <span className="num">{formatDateTime(e.starts_at)} → {formatDateTime(e.expires_at)}</span>, sortValue: (e) => e.starts_at },
    { key: "active", header: "State", cell: (e) => (e.active ? <StatusPill tone="ready" size="sm">active</StatusPill> : <StatusPill tone="done" size="sm">ended</StatusPill>) },
    { key: "reason", header: "Reason", cell: (e) => e.reason },
    { key: "act", header: "", align: "right", cell: (e) => (canCancel && e.active ? <button type="button" className="btn btn-sm btn-ghost" onClick={() => onCancel({ kind: "expedite", id: e.expedite_id, label: `Expedite +${e.boost_points} (${e.expedite_id})` })}>Cancel</button> : null) },
  ];
  const lockColumns: Column<ScheduleLock>[] = [
    { key: "type", header: "Lock", cell: (l) => <StatusPill tone="hold" size="sm">{LOCK_TYPE_LABELS[l.lock_type]}</StatusPill> },
    { key: "machine", header: "Machine", cell: (l) => <span className="mono">{l.machine_id ?? "—"}</span> },
    { key: "window", header: "Window", cell: (l) => <span className="num">{l.window ? `${formatDateTime(l.window.start, "dd MMM HH:mm")} → ${formatDateTime(l.window.end, "dd MMM HH:mm")}` : "—"}</span> },
    { key: "by", header: "By", cell: (l) => l.created_by },
    { key: "at", header: "At", cell: (l) => <span className="num">{formatDateTime(l.created_at)}</span>, sortValue: (l) => l.created_at },
    { key: "active", header: "State", cell: (l) => (l.active ? <StatusPill tone="ready" size="sm">active</StatusPill> : <StatusPill tone="done" size="sm">released</StatusPill>) },
    { key: "reason", header: "Reason", cell: (l) => l.reason },
    { key: "act", header: "", align: "right", cell: (l) => (canCancel && l.active ? <button type="button" className="btn btn-sm btn-ghost" onClick={() => onCancel({ kind: "lock", id: l.lock_id, label: `${LOCK_TYPE_LABELS[l.lock_type]} (${l.lock_id})` })}>Release</button> : null) },
  ];
  return (
    <div className="col gap-3">
      <Section title="Priority overrides" count={overrides.length} flush>
        <DataTable rows={overrides} columns={overrideColumns} rowKey={(x) => x.override_id} dense pageSize={50} emptyMessage="No overrides" initialSort={{ key: "at", direction: "desc" }} />
      </Section>
      <Section title="Expedites" count={expedites.length} flush>
        <DataTable rows={expedites} columns={expediteColumns} rowKey={(x) => x.expedite_id} dense pageSize={50} emptyMessage="No expedites" initialSort={{ key: "window", direction: "desc" }} />
      </Section>
      <Section title="Locks" count={locks.length} flush>
        <DataTable rows={locks} columns={lockColumns} rowKey={(x) => x.lock_id} dense pageSize={50} emptyMessage="No locks" initialSort={{ key: "at", direction: "desc" }} />
      </Section>
    </div>
  );
}

function AuditTrail({ entries, orderId }: { entries: AuditEntry[]; orderId: string }) {
  const [open, setOpen] = useState<AuditEntry | null>(null);
  const columns: Column<AuditEntry>[] = [
    { key: "ts", header: "When", cell: (e) => <span className="num text-nowrap">{formatDateTime(e.timestamp, "dd MMM yyyy HH:mm:ss")}</span>, sortValue: (e) => e.timestamp },
    { key: "user", header: "User", cell: (e) => <span className="mono">{e.user_id}</span> },
    { key: "action", header: "Action", cell: (e) => <span className="strong">{humanize(e.action)}</span> },
    { key: "entity", header: "Entity", cell: (e) => <span><span className="text-muted">{e.entity_type} </span><span className="mono">{e.entity_id}</span></span> },
    { key: "reason", header: "Reason", cell: (e) => <span className="truncate" style={{ maxWidth: 320, display: "inline-block" }} title={e.reason ?? undefined}>{e.reason ?? "—"}</span> },
    { key: "diff", header: "Change", cell: (e) => <span className="text-faint text-xs">{e.previous_value || e.new_value ? "view diff" : "—"}</span> },
  ];
  return (
    <>
      <Section
        title="Audit trail"
        count={entries.length}
        actions={
          <Link className="btn btn-sm" to={`${routes.audit}?entity_id=${encodeURIComponent(orderId)}`}>
            Open in audit log
          </Link>
        }
        flush
      >
        <DataTable rows={entries} columns={columns} rowKey={(e) => e.audit_id} onRowClick={setOpen} dense pageSize={50} initialSort={{ key: "ts", direction: "desc" }} emptyMessage="No audited changes for this order yet" />
      </Section>
      <Modal open={open !== null} title={open ? `${humanize(open.action)} · ${open.entity_type} ${open.entity_id}` : ""} onClose={() => setOpen(null)} wide>
        {open ? (
          <div className="col gap-3">
            <dl className="kv">
              <dt>When</dt>
              <dd className="num">{formatDateTime(open.timestamp, "dd MMM yyyy HH:mm:ss")}</dd>
              <dt>User</dt>
              <dd className="mono">{open.user_id}</dd>
              <dt>Reason</dt>
              <dd>{open.reason ?? "—"}</dd>
              {open.request_id ? (
                <>
                  <dt>Request</dt>
                  <dd className="mono text-faint">{open.request_id}</dd>
                </>
              ) : null}
            </dl>
            <JsonDiff before={open.previous_value} after={open.new_value} />
          </div>
        ) : null}
      </Modal>
    </>
  );
}

export default function OrderDetailPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const { hasMinRole } = useAuth();
  const now = useMemo(() => new Date(), []);
  const detail = useOrder(orderId);
  const machines = useOrderMachines(orderId);
  const cancel = useCancelOverlay();
  const [cancelTarget, setCancelTarget] = useState<(Omit<OverlayCancelInput, "reason"> & { label: string }) | null>(null);
  const [request, setRequest] = useState<{ action: OrderActionKind; machineId?: string | null } | null>(null);
  const canManage = hasMinRole("production_manager");

  const confirmCancel = async (reason: string) => {
    if (!cancelTarget) return;
    try {
      await cancel.mutateAsync({ ...cancelTarget, reason });
      toast.push({ tone: "success", title: "Cancelled", message: cancelTarget.label });
      setCancelTarget(null);
    } catch (err) {
      toast.push({ tone: "error", title: "Cancel failed", message: describeError(err) });
    }
  };

  return (
    <div className="page" data-testid="order-detail-page">
      <AsyncContent query={detail} loadingLabel="Loading order">
        {(d) => {
          const o = d.order;
          const p = d.priority;
          const hDue = hoursUntil(o.due_date, now);
          const lateness = p?.projected_lateness_hours ?? d.schedule?.expected_lateness_hours ?? null;
          const completion = p?.projected_completion ?? d.schedule?.expected_completion ?? null;
          const eligible = machines.data?.eligible ?? [];
          const dqBlocking = d.data_quality_issues.filter((i) => i.severity === "blocking");
          return (
            <>
              <PageHeader
                eyebrow={`Order · ${d.customer?.customer_name ?? o.customer_id}${o.external_order_ref ? ` · ERP ${o.external_order_ref}` : ""}`}
                title={o.order_id}
                subtitle={
                  <span className="row row-wrap">
                    <OrderStatusPill status={o.order_status} />
                    <ReadinessPill state={p?.readiness ?? null} />
                    <RiskBadge level={p?.risk_level ?? null} />
                    {p?.forced_next ? <span className="pill pill-hold pill-sm">FORCED NEXT</span> : null}
                    {o.on_hold ? <span className="pill pill-hold pill-sm" title={o.hold_reason ?? undefined}>ON HOLD{o.hold_reason ? ` · ${o.hold_reason}` : ""}</span> : null}
                    <span>
                      {o.part_name ?? o.part_id}
                      {o.part_family ? ` · ${o.part_family}` : ""} · {humanize(o.process_type)}
                      {o.machine_group ? ` · ${o.machine_group}` : ""}
                    </span>
                  </span>
                }
                actions={
                  <>
                    <OrderActions detail={d} eligible={eligible} request={request} onRequestHandled={() => setRequest(null)} />
                    <button type="button" className="btn" onClick={() => navigate(-1)}>
                      Back
                    </button>
                  </>
                }
              />

              <div className="grid grid-kpi">
                <KpiCard label="Priority score" value={p ? <ScoreBar value={p.score} width={110} /> : <span className="text-faint">not scored</span>} tone={p ? (p.score >= 85 ? "late" : p.score >= 65 ? "blocked" : "neutral") : "neutral"} hint={p?.rank !== null && p?.rank !== undefined ? `rank #${p.rank} · profile ${p.profile_id} v${p.profile_version}` : "run the priority engine"} />
                <KpiCard label="Due" value={formatDateTime(o.due_date, "dd MMM HH:mm")} tone={hDue !== null && hDue < 0 ? "late" : hDue !== null && hDue < 24 ? "at-risk" : "neutral"} hint={o.due_date ? (hDue !== null && hDue < 0 ? `overdue by ${formatHours(-hDue, 0)}` : formatRelative(o.due_date, now)) : "no due date"} />
                <KpiCard label="Expected completion" value={formatDateTime(completion, "dd MMM HH:mm")} tone={lateness !== null && lateness > 0 ? "late" : completion ? "ready" : "neutral"} hint={lateness !== null && lateness > 0 ? `late by ${formatHours(lateness)}` : completion ? "on time" : "not scheduled"} />
                <KpiCard label="Pending qty" value={formatNumber(o.pending_quantity)} unit={`/ ${formatNumber(o.quantity)}`} tone="neutral" hint={`${formatNumber(o.completed_quantity)} completed`} />
                <KpiCard label="Order value" value={formatCurrency(o.order_value)} tone="neutral" hint={o.estimated_margin !== null ? `margin ${formatCurrency(o.estimated_margin)}` : undefined} />
                <KpiCard label="Production time" value={formatMinutes(d.production_minutes)} tone="neutral" hint={`${d.operations.length} operations`} />
                <KpiCard label="Risk" value={<RiskBadge level={p?.risk_level ?? null} />} tone={p?.risk_level === "critical" ? "late" : p?.risk_level === "high" ? "blocked" : "neutral"} hint={p?.hours_until_due !== null && p?.hours_until_due !== undefined ? `${formatHours(p.hours_until_due)} until due (engine)` : undefined} />
              </div>

              {p?.blocked || dqBlocking.length > 0 ? (
                <div className="explain-blockers" role="status">
                  <strong>
                    Blocked
                    {p ? ` — ${READINESS_LABELS[p.readiness] ?? p.readiness}` : ""}
                    {dqBlocking.length > 0 ? ` — ${dqBlocking.length} blocking data quality issue${dqBlocking.length > 1 ? "s" : ""}` : ""}
                  </strong>
                  <ul>
                    {(p?.blocking_reasons ?? []).map((r, i) => (
                      <li key={`b${i}`}>{r}</li>
                    ))}
                    {dqBlocking.map((i, k) => (
                      <li key={`dq${k}`}>
                        {i.message}
                        {i.recommendation ? <span className="text-muted"> — {i.recommendation}</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="grid grid-main-side">
                <div className="col gap-3">
                  <Section title="Why is this order prioritised?" count={p ? `${formatScore(p.score)} / 100` : undefined} actions={p ? <span className="text-xs text-faint">computed {formatDateTime(p.computed_at, "dd MMM HH:mm")}</span> : null}>
                    {p ? (
                      <div className="col gap-3">
                        <ExplanationLines lines={d.breakdown} score={p.score} />
                        {d.breakdown.length === 0 && p.explanation ? <pre className="explain-text">{p.explanation}</pre> : null}
                      </div>
                    ) : (
                      <EmptyState compact title="Not scored yet" message="No priority result is stored for this order. Generate a schedule to run the priority engine; the explanation is produced by the same run that computes the score." />
                    )}
                  </Section>

                  <Section title="Production · route" count={d.operations.length} flush>
                    <DataTable rows={d.operations} columns={operationColumns} rowKey={(op) => op.operation_id} initialSort={{ key: "seq", direction: "asc" }} dense pageSize={100} emptyMessage="No operations from the ERP (data quality issue: missing_operations)" />
                  </Section>

                  <Section
                    title="Schedule"
                    count={d.schedule ? `v${d.schedule.version_number} · ${d.schedule.status}` : undefined}
                    actions={
                      d.schedule?.machine_id ? (
                        <Link className="btn btn-sm" to={routes.machineDetail(encodeURIComponent(d.schedule.machine_id))}>
                          Open {d.schedule.machine_id}
                        </Link>
                      ) : null
                    }
                    flush
                  >
                    {d.schedule ? (
                      <>
                        <div className="row row-wrap gap-4 text-sm" style={{ padding: "var(--sp-2) var(--sp-3)" }}>
                          <span>
                            Machine <span className="mono strong">{d.schedule.machine_id ?? "—"}</span>
                          </span>
                          <span className="num">
                            {formatDateTime(d.schedule.start, "dd MMM HH:mm")} → {formatDateTime(d.schedule.end, "dd MMM HH:mm")}
                          </span>
                          <span className={d.schedule.expected_lateness_hours && d.schedule.expected_lateness_hours > 0 ? "tone-late strong" : "tone-ready"}>
                            {d.schedule.expected_lateness_hours && d.schedule.expected_lateness_hours > 0 ? `late by ${formatHours(d.schedule.expected_lateness_hours)}` : "on time"}
                          </span>
                        </div>
                        <DataTable rows={d.schedule.entries} columns={entryColumns} rowKey={(e) => e.entry_id} initialSort={{ key: "start", direction: "asc" }} dense pageSize={50} emptyMessage="No entries in the current schedule" />
                      </>
                    ) : (
                      <EmptyState compact title="Not in the current schedule" message="The order has no placement in the current schedule version (unscheduled, blocked, or no schedule generated yet)." />
                    )}
                  </Section>

                  <Section
                    title="Machine options"
                    count={machines.data ? `${machines.data.eligible.length} eligible · ${Object.keys(machines.data.rejected).length} rejected` : undefined}
                    actions={machines.data ? <span className="text-xs text-faint">{machines.data.source} · op {machines.data.operation_id}{machines.data.synthetic_operation ? " (derived)" : ""} · {formatDateTime(machines.data.evaluated_at, "HH:mm:ss")}</span> : null}
                    flush
                  >
                    {machines.isPending ? (
                      <div style={{ padding: "var(--sp-3)" }} className="text-muted text-sm">
                        Evaluating eligible machines…
                      </div>
                    ) : machines.isError ? (
                      <div style={{ padding: "var(--sp-3)" }} className="tone-late text-sm">
                        {isApiError(machines.error) && machines.error.isNotFound ? "No operation to evaluate for this order." : describeError(machines.error)}
                      </div>
                    ) : (
                      <div className="col">
                        {machines.data.recommended_machine_id || machines.data.scheduled_machine_id || machines.data.pinned_machine_id ? (
                          <div className="row row-wrap gap-4 text-sm" style={{ padding: "var(--sp-2) var(--sp-3)" }}>
                            {machines.data.recommended_machine_id ? <span>Recommended <span className="mono strong tone-ready">{machines.data.recommended_machine_id}</span></span> : null}
                            {machines.data.scheduled_machine_id ? <span>Scheduled <span className="mono strong">{machines.data.scheduled_machine_id}</span></span> : null}
                            {machines.data.pinned_machine_id ? <span>Pinned <span className="mono strong tone-hold">{machines.data.pinned_machine_id}</span>{machines.data.pinned_by ? ` by ${machines.data.pinned_by}` : ""}</span> : null}
                          </div>
                        ) : null}
                        <DataTable
                          rows={machines.data.eligible}
                          columns={candidateColumns(canManage ? (m, action) => setRequest({ action, machineId: m.machine_id }) : null)}
                          rowKey={(c) => c.machine_id}
                          initialSort={{ key: "rank", direction: "asc" }}
                          dense
                          pageSize={50}
                          onRowClick={(c) => navigate(routes.machineDetail(encodeURIComponent(c.machine_id)))}
                          rowClassName={(c) => (c.recommended ? "row-ready" : undefined)}
                          emptyMessage="No eligible machine — see the rejected list"
                        />
                        {Object.keys(machines.data.rejected).length > 0 ? (
                          <div style={{ padding: "var(--sp-3)" }}>
                            <h3>Rejected machines</h3>
                            <dl className="kv mt-2">
                              {Object.entries(machines.data.rejected).map(([mid, reasons]) => (
                                <div key={mid} style={{ display: "contents" }}>
                                  <dt className="mono">{mid}</dt>
                                  <dd className="text-muted">{reasons.join(" · ")}</dd>
                                </div>
                              ))}
                            </dl>
                          </div>
                        ) : null}
                      </div>
                    )}
                  </Section>

                  <OverlayTables overrides={d.overrides} expedites={d.expedites} locks={d.locks} canCancel={canManage} onCancel={setCancelTarget} />

                  <AuditTrail entries={d.audit} orderId={o.order_id} />
                </div>

                <div className="col gap-3">
                  <Section title="Customer">
                    <dl className="kv">
                      <dt>Name</dt>
                      <dd>
                        {d.customer?.customer_name ?? o.customer_id} <span className="text-faint mono">{o.customer_id}</span>
                      </dd>
                      <dt>Tier</dt>
                      <dd>
                        {d.customer ? humanize(d.customer.customer_tier) : "—"}
                        {d.customer?.strategic_customer_flag ? <span className="pill pill-ready pill-sm" style={{ marginLeft: 6 }}>STRATEGIC</span> : null}
                      </dd>
                      <dt>SLA</dt>
                      <dd className="num">{d.customer?.sla_hours !== null && d.customer?.sla_hours !== undefined ? formatHours(d.customer.sla_hours) : "—"}</dd>
                      <dt>ERP priority</dt>
                      <dd className="num">{o.erp_priority ?? "—"}</dd>
                    </dl>
                  </Section>
                  <Section title="Commercial">
                    <dl className="kv">
                      <dt>Order value</dt>
                      <dd className="num">{formatCurrency(o.order_value)}</dd>
                      <dt>Est. margin</dt>
                      <dd className="num">
                        {formatCurrency(o.estimated_margin)}
                        {o.order_value && o.estimated_margin !== null ? <span className="text-faint"> ({formatPct((100 * o.estimated_margin) / o.order_value, 0)})</span> : null}
                      </dd>
                      <dt>Quantity</dt>
                      <dd className="num">
                        {formatNumber(o.quantity)} ordered · {formatNumber(o.completed_quantity)} done · {formatNumber(o.pending_quantity)} pending
                      </dd>
                    </dl>
                  </Section>
                  <Section title="Delivery">
                    <dl className="kv">
                      <dt>Order date</dt>
                      <dd className="num">{formatDateTime(o.order_date, "dd MMM yyyy")}</dd>
                      <dt>Due date</dt>
                      <dd className={`num ${hDue !== null && hDue < 0 ? "tone-late strong" : ""}`}>{formatDateTime(o.due_date, "dd MMM yyyy HH:mm")}</dd>
                      <dt>Expected</dt>
                      <dd className="num">{formatDateTime(completion, "dd MMM yyyy HH:mm")}</dd>
                      <dt>Lateness</dt>
                      <dd className={lateness !== null && lateness > 0 ? "tone-late strong" : ""}>{lateness === null ? "—" : lateness > 0 ? `+${formatHours(lateness)}` : "on time"}</dd>
                      <dt>Production status</dt>
                      <dd className="mono">{o.production_status ?? "—"}</dd>
                      <dt>Quality</dt>
                      <dd>{humanize(o.quality_status)}</dd>
                      <dt>Drawing</dt>
                      <dd>{d.drawing_approved ? "approved" : <span className="tone-blocked">not approved</span>}</dd>
                    </dl>
                  </Section>
                  <Section title="Materials & tooling">
                    <dl className="kv">
                      <dt>Required</dt>
                      <dd>
                        <span className="mono">{o.required_material_id ?? "—"}</span> · <span className={o.material_status === "available" ? "tone-ready" : "tone-blocked"}>{humanize(o.material_status)}</span>
                      </dd>
                      {d.materials.map((m) => (
                        <div key={m.material_id} style={{ display: "contents" }}>
                          <dt className="mono">{m.material_id}</dt>
                          <dd>
                            {m.material_name} · free <span className="num">{formatNumber(m.free_quantity, 1)} {m.unit}</span> (avail {formatNumber(m.available_quantity, 1)}, reserved {formatNumber(m.reserved_quantity, 1)}
                            {m.incoming_quantity > 0 ? `, incoming ${formatNumber(m.incoming_quantity, 1)}${m.expected_receipt_date ? ` on ${formatDateTime(m.expected_receipt_date, "dd MMM")}` : ""}` : ""})
                          </dd>
                        </div>
                      ))}
                      {d.tooling.map((t) => (
                        <div key={t.tooling_id} style={{ display: "contents" }}>
                          <dt className="mono">{t.tooling_id}</dt>
                          <dd>
                            {t.tooling_name} · <span className={t.usable ? "tone-ready" : "tone-blocked"}>{t.usable ? "usable" : t.available ? `unusable (${t.maintenance_status})` : "unavailable"}</span>
                            {t.available_from ? <span className="text-faint"> from {formatDateTime(t.available_from, "dd MMM HH:mm")}</span> : null}
                          </dd>
                        </div>
                      ))}
                      {d.materials.length === 0 && d.tooling.length === 0 ? (
                        <>
                          <dt>Detail</dt>
                          <dd className="text-faint">no material/tooling master data linked</dd>
                        </>
                      ) : null}
                    </dl>
                  </Section>
                  <Section title="Dependencies" count={d.dependencies.length + d.dependents.length}>
                    <dl className="kv">
                      <dt>Depends on</dt>
                      <dd>{d.dependencies.length ? d.dependencies.map((id) => <Link key={id} className="mono" to={routes.orderDetail(encodeURIComponent(id))} style={{ marginRight: 8 }}>{id}</Link>) : <span className="text-faint">none</span>}</dd>
                      <dt>Blocks</dt>
                      <dd>{d.dependents.length ? d.dependents.map((id) => <Link key={id} className="mono" to={routes.orderDetail(encodeURIComponent(id))} style={{ marginRight: 8 }}>{id}</Link>) : <span className="text-faint">none</span>}</dd>
                    </dl>
                  </Section>
                  {d.data_quality_issues.length > 0 ? (
                    <Section title="Data quality" count={d.data_quality_issues.length}>
                      <ul className="reason-list text-sm">
                        {d.data_quality_issues.map((i, k) => (
                          <li key={k}>
                            <DataQualitySeverityBadge severity={i.severity} /> <span className="mono">{i.code}</span> — {i.message}
                            {i.recommendation ? <div className="text-muted text-xs">{i.recommendation}</div> : null}
                          </li>
                        ))}
                      </ul>
                    </Section>
                  ) : null}
                  {d.special_instructions ? (
                    <Section title="Special instructions">
                      <div className="text-sm" style={{ whiteSpace: "pre-wrap" }}>
                        {d.special_instructions}
                      </div>
                    </Section>
                  ) : null}
                </div>
              </div>

              <ActionDialog
                open={cancelTarget !== null}
                title={cancelTarget ? `Cancel ${cancelTarget.label}` : ""}
                description="Cancelling ends the overlay now; the priority engine and scheduler return to their own decision on the next run. Recorded in the audit log."
                submitLabel="Cancel overlay"
                danger
                busy={cancel.isPending}
                error={cancel.error}
                onSubmit={(reason) => void confirmCancel(reason)}
                onClose={() => setCancelTarget(null)}
              />
            </>
          );
        }}
      </AsyncContent>
    </div>
  );
}
