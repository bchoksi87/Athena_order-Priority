/**
 * Production Control Tower — the morning screen (spec FINAL PRODUCT PRINCIPLE). One page that answers:
 * what to produce (first), on which machines, what is at risk, what is blocking, where the bottleneck is,
 * what additional capacity is required, which orders will miss delivery, what to do — plus the plan bar
 * (version, quality, generate / approve / publish / reject, evaluate replan with old-vs-new comparison).
 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAlerts } from "@/api/alerts";
import { useBottlenecks, useCapacity, useKpis, useScheduleQuality } from "@/api/analytics";
import { useDataQualitySummary } from "@/api/dataQuality";
import { useOrders } from "@/api/orders";
import { useSchedulePlan } from "@/api/schedule";
import type { Alert, AlertSeverity, AlertType, CapacityTotals, OrderListItem } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { BarList, type BarListItem } from "@/components/BarList";
import { BottleneckCard } from "@/components/BottleneckCard";
import { DataTable, type Column } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";
import { MenuButton } from "@/components/MenuButton";
import { PageHeader } from "@/components/PageHeader";
import { SeverityBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { WhyDrawer } from "@/components/WhyDrawer";
import { ALERT_SEVERITY_RANK, humanize } from "@/lib/constants";
import { formatCurrency, formatHours, formatNumber, formatPct } from "@/lib/formatters";
import { formatDateTime, formatRelative } from "@/lib/time";
import { useLocalStorage } from "@/lib/useLocalStorage";

import { customerColumn, dueColumn, latenessColumn, machineColumn, orderIdColumn, orderRowClass, partColumn, projectedColumn, quantityColumn, rankColumn, readinessColumn, riskColumn, scoreColumn, valueColumn } from "../shared/orderColumns";
import { OrderActionDialog } from "../shared/OrderActionDialog";
import { visibleOrderActions, type OrderActionKind } from "../shared/orderActionSpecs";
import { PlanBar } from "./PlanBar";
import "./ControlTower.css";

const TOP_N_OPTIONS = [10, 15, 25, 50] as const;
const LATE_ALERT_TYPES: AlertType[] = ["order_likely_late", "order_overdue", "sla_breach_risk"];

interface ActionGroup {
  type: AlertType;
  severity: AlertSeverity;
  count: number;
  actions: string[];
  refs: string[];
}

/** Alerts grouped by type: the strongest severity, the distinct recommended actions and a few references. */
function groupActions(alerts: Alert[]): ActionGroup[] {
  const groups = new Map<AlertType, ActionGroup>();
  for (const a of alerts) {
    const g = groups.get(a.alert_type) ?? { type: a.alert_type, severity: a.severity, count: 0, actions: [], refs: [] };
    g.count += 1;
    if (ALERT_SEVERITY_RANK[a.severity] > ALERT_SEVERITY_RANK[g.severity]) g.severity = a.severity;
    const action = a.recommended_action.trim();
    if (action && !g.actions.includes(action) && g.actions.length < 3) g.actions.push(action);
    const ref = a.order_id ?? a.machine_id ?? a.entity_ref;
    if (ref && !g.refs.includes(ref) && g.refs.length < 4) g.refs.push(ref);
    groups.set(a.alert_type, g);
  }
  return Array.from(groups.values()).sort((x, y) => ALERT_SEVERITY_RANK[y.severity] - ALERT_SEVERITY_RANK[x.severity] || y.count - x.count);
}

const lateAlertColumns = (now: Date): Column<Alert>[] => [
  { key: "sev", header: "Sev", width: 80, cell: (a) => <SeverityBadge severity={a.severity} />, sortValue: (a) => ALERT_SEVERITY_RANK[a.severity] },
  { key: "order", header: "Order", cell: (a) => <span className="mono strong">{a.order_id ?? a.entity_ref ?? "—"}</span>, sortValue: (a) => a.order_id ?? "" },
  { key: "why", header: "Why", cell: (a) => <span className="truncate" style={{ maxWidth: 300, display: "inline-block" }} title={`${a.title} — ${a.reason}`}>{a.reason || a.title}</span> },
  { key: "action", header: "Recommended action", cell: (a) => <span className="truncate text-muted" style={{ maxWidth: 260, display: "inline-block" }} title={a.recommended_action}>{a.recommended_action}</span> },
  { key: "when", header: "Raised", cell: (a) => <span className="text-faint text-nowrap">{formatRelative(a.raised_at, now)}</span>, sortValue: (a) => a.raised_at },
];

const capacityGapColumns: Column<CapacityTotals>[] = [
  { key: "key", header: "Process / resource", cell: (r) => <span className="strong">{humanize(r.key)}</span>, sortValue: (r) => r.key },
  { key: "req", header: "Required", numeric: true, cell: (r) => formatHours(r.required_hours, 0), sortValue: (r) => r.required_hours },
  { key: "avail", header: "Available", numeric: true, cell: (r) => formatHours(r.available_hours, 0), sortValue: (r) => r.available_hours },
  { key: "gap", header: "Gap", numeric: true, cell: (r) => <span className={r.gap_hours < 0 ? "tone-late strong" : "tone-ready"}>{r.gap_hours > 0 ? "+" : ""}{formatHours(r.gap_hours, 0)}</span>, sortValue: (r) => r.gap_hours },
  { key: "load", header: "Load", numeric: true, cell: (r) => <span className={r.utilization_pct > 100 ? "tone-late" : r.utilization_pct >= 85 ? "tone-at-risk" : ""}>{formatPct(r.utilization_pct, 0)}</span>, sortValue: (r) => r.utilization_pct },
];

export default function ControlTowerPage() {
  const navigate = useNavigate();
  const { hasMinRole } = useAuth();
  const now = useMemo(() => new Date(), []);
  const [topN, setTopN] = useLocalStorage<number>("ppse.ct.topN", 15);
  const [whyOrderId, setWhyOrderId] = useState<string | null>(null);
  const [pending, setPending] = useState<{ orderId: string; action: OrderActionKind; onHold: boolean } | null>(null);

  const plan = useSchedulePlan({ page_size: 1 }, 120_000);
  const quality = useScheduleQuality(120_000);
  const kpis = useKpis(60_000);
  const queue = useOrders({ sort: "rank", order: "asc", page_size: topN });
  const atRisk = useOrders({ sort: "risk", order: "desc", page_size: 12 });
  const openAlerts = useAlerts({ acknowledged: false, page_size: 200 }, 60_000);
  const lateAlerts = useAlerts({ alert_type: "order_likely_late", acknowledged: false, page_size: 15 }, 60_000);
  const overdueAlerts = useAlerts({ alert_type: "order_overdue", acknowledged: false, page_size: 15 }, 60_000);
  const bottlenecks = useBottlenecks(120_000);
  const capacity = useCapacity({ dimension: "process", period: "week" });
  const dataQuality = useDataQualitySummary();

  const k = kpis.data?.kpis;
  const rows = useMemo(() => queue.data?.items ?? [], [queue.data]);
  const missRows = useMemo(() => {
    const seen = new Set<string>();
    return [...(overdueAlerts.data?.items ?? []), ...(lateAlerts.data?.items ?? [])].filter((a) => {
      if (seen.has(a.alert_id)) return false;
      seen.add(a.alert_id);
      return true;
    });
  }, [overdueAlerts.data, lateAlerts.data]);
  const actionGroups = useMemo(() => groupActions(openAlerts.data?.items ?? []), [openAlerts.data]);
  const capacityGaps = useMemo(() => (capacity.data?.totals ?? []).filter((t) => t.gap_hours < 0 || t.shortfall_hours > 0).sort((a, b) => a.gap_hours - b.gap_hours), [capacity.data]);

  const blocking = useMemo<BarListItem[]>(() => {
    if (!k) return [];
    const items: BarListItem[] = [
      { key: "material", label: "Waiting material", value: k.blocked_by_material, tone: "blocked", onClick: () => navigate(`${routes.priorityQueue}?readiness=waiting_material`) },
      { key: "tooling", label: "Waiting tooling", value: k.blocked_by_tooling, tone: "blocked", onClick: () => navigate(`${routes.priorityQueue}?readiness=waiting_tooling`) },
      { key: "machine", label: "Machine unavailable", value: k.blocked_by_machine, tone: "late", onClick: () => navigate(`${routes.priorityQueue}?readiness=machine_unavailable`) },
      { key: "approval", label: "Waiting approval", value: k.waiting_for_approval, tone: "hold", onClick: () => navigate(`${routes.priorityQueue}?readiness=waiting_approval`) },
      { key: "unscheduled", label: "Unscheduled (no slot)", value: k.unscheduled_orders, tone: "at-risk", onClick: () => navigate(routes.gantt) },
    ];
    return items.filter((i) => i.value > 0);
  }, [k, navigate]);

  const openAction = (o: OrderListItem, action: OrderActionKind) => setPending({ orderId: o.order_id, action, onHold: o.on_hold });

  const queueColumns = useMemo<Column<OrderListItem>[]>(
    () => [
      rankColumn(),
      orderIdColumn(),
      customerColumn(),
      partColumn(),
      dueColumn(now),
      quantityColumn(),
      machineColumn(),
      scoreColumn(),
      riskColumn(),
      readinessColumn(),
      latenessColumn(),
      {
        key: "actions",
        header: "",
        width: 110,
        align: "right",
        cell: (o) => {
          const items = visibleOrderActions(hasMinRole, o.on_hold).map((a) => ({ key: a.kind, label: a.label, danger: a.danger, onSelect: () => openAction(o, a.kind) }));
          return (
            <span className="row gap-1" style={{ justifyContent: "flex-end" }} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
              <button type="button" className={`btn btn-sm${whyOrderId === o.order_id ? " btn-primary" : ""}`} onClick={() => setWhyOrderId(o.order_id)} aria-label={`Why is ${o.order_id} prioritised?`}>
                Why?
              </button>
              {items.length > 0 ? <MenuButton label="⋯" ariaLabel={`Actions for ${o.order_id}`} items={items} /> : null}
            </span>
          );
        },
      },
    ],
    [now, hasMinRole, whyOrderId],
  );

  const riskColumns = useMemo<Column<OrderListItem>[]>(() => [orderIdColumn(), customerColumn(), dueColumn(now), projectedColumn(), latenessColumn(), valueColumn(), riskColumn()], [now]);

  const scored = rows.some((o) => o.priority_score !== null);
  const dq = dataQuality.data?.dashboard;

  return (
    <div className="page" data-testid="control-tower">
      <PageHeader
        eyebrow="Plan"
        title="Production Control Tower"
        subtitle="What to produce next, on which machine, what is at risk, what is blocking, where the bottleneck is and what to do about it — every morning, from the active plan."
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => void Promise.all([kpis.refetch(), queue.refetch(), plan.refetch(), quality.refetch(), openAlerts.refetch(), bottlenecks.refetch()])} disabled={kpis.isFetching || queue.isFetching}>
              {kpis.isFetching || queue.isFetching ? "Refreshing…" : "Refresh"}
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={() => navigate(routes.priorityQueue)}>
              Full priority queue
            </button>
          </>
        }
      />

      <PlanBar plan={plan.data} quality={quality.data} loading={plan.isPending} />

      <div className="grid grid-kpi" data-testid="ct-kpis">
        <KpiCard label="Open orders" value={formatNumber(k?.total_open_orders)} loading={kpis.isPending} tone="neutral" onClick={() => navigate(routes.priorityQueue)} hint={k ? `${formatNumber(k.total_pending_quantity)} units pending` : undefined} />
        <KpiCard label="Due today" value={formatNumber(k?.orders_due_today)} loading={kpis.isPending} tone={(k?.orders_due_today ?? 0) > 0 ? "at-risk" : "neutral"} hint={`${formatNumber(k?.orders_due_tomorrow)} tomorrow · ${formatNumber(k?.orders_due_this_week)} this week`} />
        <KpiCard label="Overdue" value={formatNumber(k?.overdue_orders)} loading={kpis.isPending} tone={(k?.overdue_orders ?? 0) > 0 ? "late" : "ready"} onClick={() => navigate(`${routes.priorityQueue}?due_to=${formatDateTime(now, "yyyy-MM-dd")}`)} />
        <KpiCard label="At risk" value={formatNumber(k?.at_risk_orders)} loading={kpis.isPending} tone={(k?.at_risk_orders ?? 0) > 0 ? "blocked" : "ready"} hint={`revenue at risk ${formatCurrency(k?.revenue_at_risk)}`} onClick={() => navigate(`${routes.priorityQueue}?risk=high`)} />
        <KpiCard label="Blocked" value={formatNumber(k?.blocked_total)} loading={kpis.isPending} tone={(k?.blocked_total ?? 0) > 0 ? "blocked" : "ready"} hint={`mat ${formatNumber(k?.blocked_by_material)} · tool ${formatNumber(k?.blocked_by_tooling)} · mach ${formatNumber(k?.blocked_by_machine)} · appr ${formatNumber(k?.waiting_for_approval)}`} />
        <KpiCard label="Expected on-time" value={formatPct(k?.expected_on_time_delivery_pct, 0)} loading={kpis.isPending} tone={(k?.expected_on_time_delivery_pct ?? 0) >= 90 ? "ready" : (k?.expected_on_time_delivery_pct ?? 0) >= 75 ? "at-risk" : "late"} hint={`historical ${formatPct(k?.on_time_delivery_pct, 0)}`} onClick={() => navigate(routes.executive)} />
        <KpiCard label="Machine utilisation" value={formatPct(k?.machine_utilization_pct, 0)} loading={kpis.isPending} tone="running" hint={`capacity ${formatPct(k?.capacity_utilization_pct, 0)}`} onClick={() => navigate(routes.capacity)} />
        <KpiCard label="Unscheduled" value={formatNumber(k?.unscheduled_orders)} loading={kpis.isPending} tone={(k?.unscheduled_orders ?? 0) > 0 ? "at-risk" : "ready"} hint={`${formatNumber(k?.scheduled_orders)} scheduled in the plan`} onClick={() => navigate(routes.gantt)} />
      </div>

      <div className="grid grid-main-side">
        <Section
          title="Produce next"
          count={`top ${topN} by rank`}
          flush
          actions={
            <>
              <span className="ct-question">What needs to be produced first, and on which machine?</span>
              <select className="select" value={topN} onChange={(e) => setTopN(Number(e.target.value))} aria-label="Top N">
                {TOP_N_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    top {n}
                  </option>
                ))}
              </select>
              {!scored && rows.length > 0 ? <span className="badge badge-writeback">not scored — generate a schedule</span> : null}
            </>
          }
        >
          <AsyncContent query={queue} isEmpty={(p) => p.items.length === 0} emptyTitle="No open orders" emptyMessage="Sync the ERP or generate synthetic data to populate the queue.">
            {() => (
              <DataTable rows={rows} columns={queueColumns} rowKey={(o) => o.order_id} selectedKey={whyOrderId} onRowClick={(o) => navigate(routes.orderDetail(encodeURIComponent(o.order_id)))} rowClassName={(o) => orderRowClass(o, now)} paginate={false} hideFooter dense ariaLabel="Produce next" />
            )}
          </AsyncContent>
        </Section>

        <div className="col gap-3">
          <Section title="What is blocking production" count={k ? formatNumber(k.blocked_total) : undefined}>
            {kpis.isPending ? (
              <span className="text-muted text-sm">Loading…</span>
            ) : blocking.length === 0 ? (
              <span className="tone-ready text-sm">Nothing is blocked — every open order is ready or scheduled.</span>
            ) : (
              <BarList items={blocking} total={k?.total_open_orders} />
            )}
            {dq ? (
              <div className="mt-2 text-sm" data-testid="dq-headline">
                <span className={dq.unschedulable_orders > 0 ? "tone-blocked strong" : "tone-ready"}>{dq.headline}</span>{" "}
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(routes.dataQuality)}>
                  Data quality
                </button>
              </div>
            ) : dataQuality.isError ? (
              <div className="mt-2 text-xs text-faint">Data-quality headline needs the planner role.</div>
            ) : null}
          </Section>

          <AsyncContent query={bottlenecks} emptyTitle="No bottleneck data" compact isEmpty={() => false}>
            {(r) => (
              <BottleneckCard bottleneck={r.current} title="Where is the bottleneck" onOpen={() => navigate(routes.bottlenecks)} openLabel="Bottleneck analysis" compact />
            )}
          </AsyncContent>

          <Section title="Additional capacity required" count={capacity.data ? `${capacity.data.period}ly · ${capacityGaps.length} short` : undefined} flush actions={<button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(routes.capacity)}>Capacity planning</button>}>
            <AsyncContent query={capacity} emptyTitle="No capacity data" compact isEmpty={() => false}>
              {(c) =>
                capacityGaps.length === 0 ? (
                  <div className="section-body tone-ready text-sm">Available hours cover the required hours for every {humanize(c.dimension).toLowerCase()} in the horizon.</div>
                ) : (
                  <DataTable rows={capacityGaps.slice(0, 8)} columns={capacityGapColumns} rowKey={(r) => r.key} rowClassName={(r) => (r.gap_hours < 0 ? "row-late" : undefined)} dense paginate={false} hideFooter onRowClick={() => navigate(`${routes.capacity}?dimension=process`)} />
                )
              }
            </AsyncContent>
          </Section>
        </div>
      </div>

      <div className="grid grid-2">
        <Section title="What is at risk" count={atRisk.data ? `${formatNumber(atRisk.data.meta.total)} open · highest risk first` : undefined} flush actions={<button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(`${routes.priorityQueue}?sort=risk&order=desc`)}>All</button>}>
          <AsyncContent query={atRisk} isEmpty={(p) => p.items.length === 0} emptyTitle="No orders at risk" compact>
            {(p) => <DataTable rows={p.items} columns={riskColumns} rowKey={(o) => o.order_id} onRowClick={(o) => navigate(routes.orderDetail(encodeURIComponent(o.order_id)))} rowClassName={(o) => orderRowClass(o, now)} paginate={false} hideFooter dense ariaLabel="Orders at risk" />}
          </AsyncContent>
        </Section>
        <Section title="Orders likely to miss delivery" count={missRows.length ? `${missRows.length} alerts` : undefined} flush actions={<button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(`${routes.alerts}?alert_type=order_likely_late`)}>Alerts</button>}>
          {lateAlerts.isPending || overdueAlerts.isPending ? (
            <div className="section-body text-muted text-sm">Loading alerts…</div>
          ) : lateAlerts.isError && overdueAlerts.isError ? (
            <div className="section-body text-muted text-sm">Late-order alerts need the supervisor role.</div>
          ) : missRows.length === 0 ? (
            <div className="section-body tone-ready text-sm">No order is flagged late or overdue by the alert engine.</div>
          ) : (
            <DataTable rows={missRows} columns={lateAlertColumns(now)} rowKey={(a) => a.alert_id} onRowClick={(a) => (a.order_id ? navigate(routes.orderDetail(encodeURIComponent(a.order_id))) : navigate(routes.alerts))} rowClassName={(a) => (a.alert_type === "order_overdue" ? "row-late" : "row-at-risk")} initialSort={{ key: "sev", direction: "desc" }} paginate={false} hideFooter dense ariaLabel="Orders likely to miss delivery" />
          )}
        </Section>
      </div>

      <Section title="Recommended actions" count={openAlerts.data ? `${formatNumber(openAlerts.data.meta.total)} open alerts` : undefined} actions={<span className="ct-question">Derived from the alert engine's recommended actions, grouped by alert type.</span>}>
        {openAlerts.isPending ? (
          <span className="text-muted text-sm">Loading…</span>
        ) : openAlerts.isError ? (
          <span className="text-muted text-sm">Alerts need the supervisor role.</span>
        ) : actionGroups.length === 0 ? (
          <span className="tone-ready text-sm">No open alerts — nothing to act on right now.</span>
        ) : (
          <div className="ct-actions" data-testid="recommended-actions">
            {actionGroups.map((g) => (
              <div key={g.type} className={`ct-action ct-action-${g.severity}`}>
                <div className="col gap-1">
                  <SeverityBadge severity={g.severity} />
                  <span className="num text-muted text-xs">×{g.count}</span>
                </div>
                <div className="ct-action-lines">
                  <span className="ct-action-type">{humanize(g.type)}</span>
                  <ul className="reason-list">
                    {g.actions.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                  {g.refs.length ? <span className="text-faint text-xs mono">{g.refs.join(" · ")}</span> : null}
                </div>
                <button type="button" className="btn btn-sm" onClick={() => navigate(`${routes.alerts}?alert_type=${g.type}`)}>
                  Open
                </button>
              </div>
            ))}
            {LATE_ALERT_TYPES.every((t) => !actionGroups.some((g) => g.type === t)) ? <span className="text-faint text-xs">No delivery alerts among the open ones.</span> : null}
          </div>
        )}
      </Section>

      <WhyDrawer
        orderId={whyOrderId}
        onClose={() => setWhyOrderId(null)}
        onOpenOrder={(id) => navigate(routes.orderDetail(encodeURIComponent(id)))}
        now={now}
        actions={(() => {
          const row = rows.find((o) => o.order_id === whyOrderId);
          const items = row ? visibleOrderActions(hasMinRole, row.on_hold).map((a) => ({ key: a.kind, label: a.label, danger: a.danger, onSelect: () => openAction(row, a.kind) })) : [];
          return items.length > 0 ? <MenuButton label="Actions" items={items} /> : null;
        })()}
      >
        {(() => {
          const row = rows.find((o) => o.order_id === whyOrderId);
          return row ? (
            <dl className="kv mb-4">
              <dt>Customer</dt>
              <dd>{row.customer_name ?? row.customer_id}</dd>
              <dt>Part</dt>
              <dd>{row.part_name ?? row.part_id}</dd>
              <dt>Due</dt>
              <dd className="num">{formatDateTime(row.due_date, "dd MMM yyyy HH:mm")}</dd>
              <dt>Machine</dt>
              <dd className="mono">{row.scheduled_machine_id ?? row.required_machine_id ?? row.machine_group ?? "—"}</dd>
              <dt>Value</dt>
              <dd className="num">{formatCurrency(row.order_value)}</dd>
            </dl>
          ) : null;
        })()}
      </WhyDrawer>

      {pending ? <OrderActionDialog orderId={pending.orderId} action={pending.action} onClose={() => setPending(null)} /> : null}
    </div>
  );
}
