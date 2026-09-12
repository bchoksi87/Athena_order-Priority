import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useOrder, useOrderExplanation, useOrderMachines } from "@/api/orders";
import type { Expedite, MachineCandidate, Operation, PriorityOverride, ScheduleEntry } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ExplanationPanel } from "@/components/ExplanationPanel";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { RiskBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { OrderStatusPill, ReadinessPill, StatusPill } from "@/components/StatusPill";
import { Tabs } from "@/components/Tabs";
import { humanize } from "@/lib/constants";
import { formatCurrency, formatHours, formatMinutes, formatNumber, formatScore } from "@/lib/formatters";
import { formatDateTime, formatDue } from "@/lib/time";

import { OrderActions } from "./OrderActions";

type TabKey = "why" | "operations" | "machines" | "schedule" | "history";

const operationColumns: Column<Operation>[] = [
  { key: "seq", header: "#", width: 40, numeric: true, cell: (o) => o.sequence, sortValue: (o) => o.sequence },
  { key: "type", header: "Operation", cell: (o) => humanize(o.operation_type), sortValue: (o) => o.operation_type },
  { key: "status", header: "Status", cell: (o) => <StatusPill tone={o.operation_status === "completed" ? "done" : o.operation_status === "in_progress" ? "running" : o.operation_status === "on_hold" ? "hold" : "neutral"} size="sm">{humanize(o.operation_status)}</StatusPill> },
  { key: "machine", header: "Machine / group", cell: (o) => <span className="mono">{o.machine_id ?? o.machine_group ?? "—"}</span> },
  { key: "eligible", header: "Eligible", cell: (o) => <span className="mono text-muted">{o.eligible_machine_ids.length ? o.eligible_machine_ids.join(", ") : "derive"}</span> },
  { key: "setup", header: "Setup", numeric: true, cell: (o) => formatMinutes(o.setup_minutes) },
  { key: "cycle", header: "Cycle/unit", numeric: true, cell: (o) => (o.cycle_minutes_per_unit === null ? "—" : `${formatNumber(o.cycle_minutes_per_unit, 2)}m`) },
  { key: "qty", header: "Done/Qty", numeric: true, cell: (o) => `${formatNumber(o.completed_quantity)}/${formatNumber(o.quantity)}` },
  { key: "material", header: "Material", cell: (o) => o.material_id ?? "—" },
  { key: "tooling", header: "Tooling", cell: (o) => (o.tooling_ids.length ? o.tooling_ids.join(", ") : "—") },
  { key: "family", header: "Setup family", cell: (o) => o.setup_family ?? "—" },
  { key: "est", header: "Est. start → end", cell: (o) => <span className="num">{formatDateTime(o.estimated_start, "dd MMM HH:mm")} → {formatDateTime(o.estimated_end, "dd MMM HH:mm")}</span> },
];

const candidateColumns: Column<MachineCandidate>[] = [
  { key: "rank", header: "Rank", width: 50, numeric: true, cell: (c) => c.rank, sortValue: (c) => c.rank },
  { key: "machine", header: "Machine", cell: (c) => <span className="mono strong">{c.machine_id}{c.recommended ? <span className="pill pill-ready pill-sm" style={{ marginLeft: 6 }}>RECOMMENDED</span> : null}</span> },
  { key: "start", header: "Expected start", cell: (c) => <span className="num">{formatDateTime(c.expected_start, "dd MMM HH:mm")}</span>, sortValue: (c) => c.expected_start ?? "9999" },
  { key: "end", header: "Expected end", cell: (c) => <span className="num">{formatDateTime(c.expected_end, "dd MMM HH:mm")}</span>, sortValue: (c) => c.expected_end ?? "9999" },
  { key: "setup", header: "Setup", numeric: true, cell: (c) => formatMinutes(c.setup_minutes), sortValue: (c) => c.setup_minutes },
  { key: "run", header: "Run", numeric: true, cell: (c) => formatMinutes(c.run_minutes), sortValue: (c) => c.run_minutes },
  { key: "cost", header: "Soft cost", numeric: true, cell: (c) => formatNumber(c.soft_cost, 0), sortValue: (c) => c.soft_cost },
  { key: "reasons", header: "Reasons", cell: (c) => <span className="text-muted">{c.reasons.join(" · ")}</span> },
];

const entryColumns: Column<ScheduleEntry>[] = [
  { key: "machine", header: "Machine", cell: (e) => <span className="mono strong">{e.machine_id}</span> },
  { key: "op", header: "Operation", cell: (e) => <span className="mono">{e.operation_id}</span> },
  { key: "seq", header: "Seq", numeric: true, cell: (e) => e.sequence_on_machine },
  { key: "setup", header: "Setup start", cell: (e) => <span className="num">{formatDateTime(e.setup_start, "dd MMM HH:mm")}</span> },
  { key: "start", header: "Start", cell: (e) => <span className="num">{formatDateTime(e.start, "dd MMM HH:mm")}</span>, sortValue: (e) => e.start },
  { key: "end", header: "End", cell: (e) => <span className="num">{formatDateTime(e.end, "dd MMM HH:mm")}</span> },
  { key: "late", header: "Lateness", numeric: true, cell: (e) => <span className={e.expected_lateness_hours && e.expected_lateness_hours > 0 ? "tone-late" : ""}>{formatHours(e.expected_lateness_hours)}</span> },
  { key: "lock", header: "Lock", width: 50, cell: (e) => (e.locked ? "🔒" : "") },
  { key: "why", header: "Placement", cell: (e) => <span className="text-muted">{e.placement_reason}</span> },
];

const overrideColumns: Column<PriorityOverride>[] = [
  { key: "type", header: "Override", cell: (o) => humanize(o.override_type) },
  { key: "value", header: "Value", numeric: true, cell: (o) => (o.value === null ? "—" : formatNumber(o.value, 1)) },
  { key: "by", header: "By", cell: (o) => o.created_by },
  { key: "at", header: "At", cell: (o) => <span className="num">{formatDateTime(o.created_at)}</span>, sortValue: (o) => o.created_at },
  { key: "exp", header: "Expires", cell: (o) => <span className="num">{formatDateTime(o.expires_at)}</span> },
  { key: "active", header: "Active", cell: (o) => (o.active ? "yes" : "no") },
  { key: "reason", header: "Reason", cell: (o) => o.reason },
];

const expediteColumns: Column<Expedite>[] = [
  { key: "points", header: "Boost", numeric: true, cell: (e) => `+${formatNumber(e.boost_points, 0)}` },
  { key: "by", header: "By", cell: (e) => e.created_by },
  { key: "window", header: "Window", cell: (e) => <span className="num">{formatDateTime(e.starts_at)} → {formatDateTime(e.expires_at)}</span>, sortValue: (e) => e.starts_at },
  { key: "active", header: "Active", cell: (e) => (e.active ? "yes" : "no") },
  { key: "reason", header: "Reason", cell: (e) => e.reason },
];

/** One order: header facts, explanation ("why?"), routing, eligible machines, schedule placement and override history. */
export default function OrderDetailPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const [tab, setTab] = useState<TabKey>("why");
  const detail = useOrder(orderId);
  const explanation = useOrderExplanation(detail.data && !detail.data.priority ? orderId : undefined);
  const machines = useOrderMachines(tab === "machines" ? orderId : undefined);

  return (
    <div className="page">
      <AsyncContent query={detail} loadingLabel="Loading order">
        {(d) => {
          const o = d.order;
          const priority = d.priority ?? explanation.data ?? null;
          const due = o.revised_delivery_date ?? o.promised_delivery_date ?? o.requested_delivery_date;
          const pending = o.quantity - o.completed_quantity - o.cancelled_quantity;
          return (
            <>
              <PageHeader
                eyebrow={`Order · ${d.customer?.customer_name ?? o.customer_id}`}
                title={o.order_id}
                subtitle={
                  <span className="row row-wrap">
                    <OrderStatusPill status={o.order_status} />
                    <ReadinessPill state={priority?.readiness ?? null} />
                    <RiskBadge level={priority?.risk_level ?? null} />
                    <span>
                      {o.part_name ?? o.part_id} {o.part_family ? `· ${o.part_family}` : ""} · {humanize(o.process_type)}
                    </span>
                    {o.external_order_ref ? <span className="text-faint">ERP {o.external_order_ref}</span> : null}
                  </span>
                }
                actions={
                  <>
                    <OrderActions detail={d} />
                    <button type="button" className="btn" onClick={() => navigate(-1)}>
                      Back
                    </button>
                  </>
                }
              />

              <div className="grid grid-kpi">
                <KpiCard label="Priority" value={formatScore(priority?.score)} tone={priority ? (priority.score >= 85 ? "late" : priority.score >= 65 ? "blocked" : "neutral") : "neutral"} hint={priority?.rank !== null && priority?.rank !== undefined ? `rank #${priority.rank}` : "not scored"} />
                <KpiCard label="Due" value={formatDateTime(due, "dd MMM HH:mm")} tone={priority?.hours_until_due !== null && priority?.hours_until_due !== undefined && priority.hours_until_due < 0 ? "late" : "at-risk"} hint={formatDue(due, now)} />
                <KpiCard label="Projected" value={formatDateTime(priority?.projected_completion, "dd MMM HH:mm")} tone={priority?.projected_lateness_hours && priority.projected_lateness_hours > 0 ? "late" : "ready"} hint={priority?.projected_lateness_hours && priority.projected_lateness_hours > 0 ? `late by ${formatHours(priority.projected_lateness_hours)}` : "on time"} />
                <KpiCard label="Pending qty" value={formatNumber(pending)} unit={`/ ${formatNumber(o.quantity)}`} tone="neutral" />
                <KpiCard label="Value" value={formatCurrency(o.order_value)} tone="neutral" hint={o.estimated_margin !== null ? `margin ${formatCurrency(o.estimated_margin)}` : undefined} />
                <KpiCard label="Est. production" value={formatMinutes(o.estimated_total_production_minutes)} tone="neutral" hint={`setup ${formatMinutes(o.estimated_setup_minutes)}`} />
              </div>

              <div className="grid grid-main-side">
                <div className="col gap-3">
                  <Tabs<TabKey>
                    value={tab}
                    onChange={setTab}
                    items={[
                      { key: "why", label: "Why?" },
                      { key: "operations", label: "Routing", count: d.operations.length },
                      { key: "machines", label: "Machines" },
                      { key: "schedule", label: "Schedule", count: d.schedule_entries.length },
                      { key: "history", label: "Overrides", count: d.overrides.length + d.expedites.length + d.locks.length },
                    ]}
                  />
                  {tab === "why" ? (
                    <Section title="Priority explanation">
                      {priority ? <ExplanationPanel result={priority} /> : explanation.isPending && !d.priority ? <LoadingState compact label="Loading explanation" /> : <EmptyState compact title="Not scored yet" message="Generate a schedule to run the priority engine." />}
                    </Section>
                  ) : null}
                  {tab === "operations" ? (
                    <Section title="Routing" flush>
                      <DataTable rows={d.operations} columns={operationColumns} rowKey={(op) => op.operation_id} initialSort={{ key: "seq", direction: "asc" }} dense pageSize={50} emptyMessage="No operations (data quality issue)" />
                    </Section>
                  ) : null}
                  {tab === "machines" ? (
                    <Section title="Eligible machines" flush>
                      <AsyncContent query={machines} isEmpty={(r) => r.eligible.length === 0 && Object.keys(r.rejected).length === 0} emptyTitle="No machine evaluation" compact>
                        {(rec) => (
                          <div className="col">
                            <DataTable rows={rec.eligible} columns={candidateColumns} rowKey={(c) => c.machine_id} initialSort={{ key: "rank", direction: "asc" }} dense pageSize={50} onRowClick={(c) => navigate(routes.machineDetail(encodeURIComponent(c.machine_id)))} emptyMessage="No eligible machine" />
                            {Object.keys(rec.rejected).length > 0 ? (
                              <div style={{ padding: "var(--sp-3)" }}>
                                <h3>Rejected</h3>
                                <dl className="kv mt-2">
                                  {Object.entries(rec.rejected).map(([mid, reasons]) => (
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
                      </AsyncContent>
                    </Section>
                  ) : null}
                  {tab === "schedule" ? (
                    <Section title="Schedule placement" flush>
                      <DataTable rows={d.schedule_entries} columns={entryColumns} rowKey={(e) => e.entry_id} initialSort={{ key: "start", direction: "asc" }} dense pageSize={50} onRowClick={(e) => navigate(routes.machineDetail(encodeURIComponent(e.machine_id)))} emptyMessage="Not in the current schedule" />
                    </Section>
                  ) : null}
                  {tab === "history" ? (
                    <div className="col gap-3">
                      <Section title="Priority overrides" count={d.overrides.length} flush>
                        <DataTable rows={d.overrides} columns={overrideColumns} rowKey={(x) => x.override_id} dense pageSize={50} emptyMessage="No overrides" initialSort={{ key: "at", direction: "desc" }} />
                      </Section>
                      <Section title="Expedites" count={d.expedites.length} flush>
                        <DataTable rows={d.expedites} columns={expediteColumns} rowKey={(x) => x.expedite_id} dense pageSize={50} emptyMessage="No expedites" initialSort={{ key: "window", direction: "desc" }} />
                      </Section>
                      <Section title="Locks" count={d.locks.length}>
                        {d.locks.length === 0 ? <span className="text-muted text-sm">No locks</span> : (
                          <ul className="text-sm" style={{ margin: 0, paddingLeft: 18 }}>
                            {d.locks.map((l) => (
                              <li key={l.lock_id}>
                                <span className="mono">{humanize(l.lock_type)}</span> by {l.created_by} at {formatDateTime(l.created_at)} — {l.reason}
                              </li>
                            ))}
                          </ul>
                        )}
                      </Section>
                    </div>
                  ) : null}
                </div>

                <div className="col gap-3">
                  <Section title="Order facts">
                    <dl className="kv">
                      <dt>Customer</dt>
                      <dd>{d.customer ? `${d.customer.customer_name} (${humanize(d.customer.customer_tier)}${d.customer.strategic_customer_flag ? ", strategic" : ""})` : o.customer_id}</dd>
                      <dt>Requested</dt>
                      <dd className="num">{formatDateTime(o.requested_delivery_date)}</dd>
                      <dt>Promised</dt>
                      <dd className="num">{formatDateTime(o.promised_delivery_date)}</dd>
                      <dt>Revised</dt>
                      <dd className="num">{formatDateTime(o.revised_delivery_date)}</dd>
                      <dt>Received</dt>
                      <dd className="num">{formatDateTime(o.received_date ?? o.order_date)}</dd>
                      <dt>SLA</dt>
                      <dd className="num">{o.sla_hours !== null ? formatHours(o.sla_hours) : d.customer?.sla_hours ? `${formatHours(d.customer.sla_hours)} (customer)` : "—"}</dd>
                      <dt>ERP priority</dt>
                      <dd className="num">{o.erp_priority ?? "—"}</dd>
                      <dt>Penalty / day</dt>
                      <dd className="num">{formatCurrency(o.lateness_penalty_per_day)}</dd>
                      <dt>Material</dt>
                      <dd>{o.required_material_id ?? "—"} · {humanize(o.material_status)}</dd>
                      <dt>Tooling</dt>
                      <dd>{o.tooling_requirement.length ? o.tooling_requirement.join(", ") : "—"}</dd>
                      <dt>Quality</dt>
                      <dd>{humanize(o.quality_status)}</dd>
                      <dt>Drawing</dt>
                      <dd>{o.drawing_approved ? "approved" : <span className="tone-blocked">not approved</span>}</dd>
                      <dt>Route</dt>
                      <dd>{o.manufacturing_route.length ? o.manufacturing_route.map(humanize).join(" → ") : humanize(o.process_type)}</dd>
                      <dt>Machine group</dt>
                      <dd className="mono">{o.machine_group ?? "—"}{o.required_machine_id ? ` (requires ${o.required_machine_id})` : ""}</dd>
                      <dt>Depends on</dt>
                      <dd className="mono">{o.depends_on_order_ids.length ? o.depends_on_order_ids.join(", ") : "—"}</dd>
                      {o.on_hold ? (
                        <>
                          <dt>Hold reason</dt>
                          <dd className="tone-hold">{o.hold_reason ?? "on hold"}</dd>
                        </>
                      ) : null}
                      {o.special_instructions ? (
                        <>
                          <dt>Instructions</dt>
                          <dd>{o.special_instructions}</dd>
                        </>
                      ) : null}
                    </dl>
                  </Section>
                  {d.blockers.length > 0 ? (
                    <Section title="Blockers" count={d.blockers.length}>
                      <ul className="text-sm" style={{ margin: 0, paddingLeft: 18 }}>
                        {d.blockers.map((b, i) => (
                          <li key={i}>
                            <ReadinessPill state={b.state} size="sm" /> {b.message}
                            {b.resolves_at ? <span className="text-faint"> · clears {formatDateTime(b.resolves_at)}</span> : null}
                          </li>
                        ))}
                      </ul>
                    </Section>
                  ) : null}
                </div>
              </div>
            </>
          );
        }}
      </AsyncContent>
    </div>
  );
}
