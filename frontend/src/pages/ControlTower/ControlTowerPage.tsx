import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { useAlerts } from "@/api/alerts";
import { useKpis } from "@/api/analytics";
import { useMachines } from "@/api/machines";
import { useOrders } from "@/api/orders";
import { useSchedule } from "@/api/schedule";
import type { Alert, MachineListItem, OrderListItem, UnscheduledItem } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { SeverityBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { MachineStatusPill, ScheduleStatusPill } from "@/components/StatusPill";
import { formatCurrency, formatPct } from "@/lib/formatters";
import { formatDateTime, formatRelative } from "@/lib/time";

import {
  customerColumn,
  dueColumn,
  machineColumn,
  orderIdColumn,
  orderRowClass,
  partColumn,
  rankColumn,
  readinessColumn,
  riskColumn,
  scoreColumn,
} from "../shared/orderColumns";

const TOP_N = 25;

const unscheduledColumns: Column<UnscheduledItem>[] = [
  { key: "order", header: "Order", cell: (u) => <span className="mono">{u.order_id}</span>, sortValue: (u) => u.order_id },
  { key: "code", header: "Code", cell: (u) => <span className="mono text-muted">{u.reason_code}</span>, sortValue: (u) => u.reason_code },
  {
    key: "reason",
    header: "Reason",
    cell: (u) => (
      <span className="truncate" style={{ maxWidth: 260, display: "inline-block" }} title={u.reason}>
        {u.reason}
      </span>
    ),
  },
];

/** "What should we produce next?" — the primary operational screen. */
export default function ControlTowerPage() {
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const kpis = useKpis();
  const queue = useOrders({ sort: "-priority_score", limit: TOP_N });
  const machines = useMachines();
  const schedule = useSchedule();
  const alerts = useAlerts({ acknowledged: false, limit: 8 });

  const queueColumns = useMemo<Column<OrderListItem>[]>(
    () => [
      rankColumn(),
      orderIdColumn(),
      partColumn(),
      customerColumn(),
      scoreColumn(),
      readinessColumn(),
      riskColumn(),
      dueColumn(now),
      machineColumn(),
    ],
    [now],
  );

  const machineColumns = useMemo<Column<MachineListItem>[]>(
    () => [
      { key: "id", header: "Machine", cell: (m) => <span className="mono strong">{m.machine_id}</span>, sortValue: (m) => m.machine_id },
      { key: "group", header: "Group", cell: (m) => m.machine_group, sortValue: (m) => m.machine_group },
      { key: "status", header: "Status", cell: (m) => <MachineStatusPill status={m.status} size="sm" />, sortValue: (m) => m.status },
      { key: "current", header: "Running", cell: (m) => <span className="mono">{m.current_order_id ?? "—"}</span> },
      { key: "queue", header: "Queue", numeric: true, cell: (m) => m.queue_length ?? "—", sortValue: (m) => m.queue_length ?? -1 },
      {
        key: "util",
        header: "Util",
        numeric: true,
        cell: (m) => formatPct(m.utilization_pct ?? (m.utilization !== null ? m.utilization * 100 : null), 0),
        sortValue: (m) => m.utilization_pct ?? -1,
      },
    ],
    [],
  );

  const alertColumns = useMemo<Column<Alert>[]>(
    () => [
      { key: "sev", header: "Sev", width: 80, cell: (a) => <SeverityBadge severity={a.severity} /> },
      {
        key: "title",
        header: "Alert",
        cell: (a) => (
          <span className="truncate" style={{ maxWidth: 320, display: "inline-block" }}>
            {a.title}
          </span>
        ),
      },
      { key: "ref", header: "Ref", cell: (a) => <span className="mono">{a.order_id ?? a.machine_id ?? a.entity_ref ?? "—"}</span> },
      { key: "when", header: "Raised", cell: (a) => <span className="text-muted">{formatRelative(a.raised_at, now)}</span> },
    ],
    [now],
  );

  const k = kpis.data;
  return (
    <div className="page">
      <PageHeader
        eyebrow="Plan"
        title="Production Control Tower"
        subtitle={
          <span>
            What should we produce next? · Schedule{" "}
            {schedule.data?.version ? (
              <>
                v{schedule.data.version.version_number} <ScheduleStatusPill status={schedule.data.version.status} size="sm" /> generated{" "}
                {formatDateTime(schedule.data.generated_at)}
              </>
            ) : (
              "not generated yet"
            )}
          </span>
        }
        actions={
          <>
            <button type="button" className="btn" onClick={() => navigate(routes.gantt)}>
              Gantt
            </button>
            <button type="button" className="btn btn-primary" onClick={() => navigate(routes.priorityQueue)}>
              Full priority queue
            </button>
          </>
        }
      />

      <div className="grid grid-kpi">
        <KpiCard label="Open orders" value={k?.total_open_orders ?? "—"} loading={kpis.isPending} tone="neutral" onClick={() => navigate(routes.priorityQueue)} />
        <KpiCard
          label="Due today"
          value={k?.orders_due_today ?? "—"}
          loading={kpis.isPending}
          tone="at-risk"
          hint={`${k?.orders_due_tomorrow ?? "—"} tomorrow · ${k?.orders_due_this_week ?? "—"} this week`}
        />
        <KpiCard label="Overdue" value={k?.overdue_orders ?? "—"} loading={kpis.isPending} tone="late" />
        <KpiCard label="At risk" value={k?.at_risk_orders ?? "—"} loading={kpis.isPending} tone="blocked" hint={`Revenue at risk ${formatCurrency(k?.revenue_at_risk)}`} />
        <KpiCard
          label="Blocked"
          value={k?.blocked_total ?? "—"}
          loading={kpis.isPending}
          tone="blocked"
          hint={`Mat ${k?.blocked_by_material ?? "—"} · Tool ${k?.blocked_by_tooling ?? "—"} · Mach ${k?.blocked_by_machine ?? "—"}`}
        />
        <KpiCard label="Expected OTD" value={formatPct(k?.expected_on_time_delivery_pct, 0)} loading={kpis.isPending} tone="ready" hint={`Actual ${formatPct(k?.on_time_delivery_pct, 0)}`} />
        <KpiCard label="Utilisation" value={formatPct(k?.machine_utilization_pct, 0)} loading={kpis.isPending} tone="running" />
        <KpiCard
          label="Unscheduled"
          value={k?.unscheduled_orders ?? "—"}
          loading={kpis.isPending}
          tone={k && k.unscheduled_orders > 0 ? "at-risk" : "neutral"}
          hint={`${k?.scheduled_orders ?? "—"} scheduled`}
        />
      </div>

      <div className="grid grid-main-side">
        <Section title="Produce next" count={`top ${TOP_N} by priority`} flush actions={<span className="text-faint text-xs">click a row for “why?”</span>}>
          <AsyncContent query={queue} isEmpty={(p) => p.items.length === 0} emptyTitle="No open orders" emptyMessage="Sync the ERP or generate synthetic data to populate the queue.">
            {(page) => (
              <DataTable
                rows={page.items}
                columns={queueColumns}
                rowKey={(o) => o.order_id}
                onRowClick={(o) => navigate(routes.orderDetail(encodeURIComponent(o.order_id)))}
                rowClassName={(o) => orderRowClass(o, now)}
                initialSort={{ key: "score", direction: "desc" }}
                pageSize={TOP_N}
                dense
              />
            )}
          </AsyncContent>
        </Section>

        <div className="col gap-3">
          <Section title="Machines" count={machines.data?.length} flush scroll style={{ maxHeight: 360 }}>
            <AsyncContent query={machines} emptyTitle="No machines" compact>
              {(rows) => (
                <DataTable
                  rows={rows}
                  columns={machineColumns}
                  rowKey={(m) => m.machine_id}
                  onRowClick={(m) => navigate(routes.machineDetail(encodeURIComponent(m.machine_id)))}
                  dense
                  pageSize={200}
                  initialSort={{ key: "status", direction: "asc" }}
                />
              )}
            </AsyncContent>
          </Section>
          <Section
            title="Open alerts"
            count={alerts.data?.meta.total}
            flush
            actions={
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(routes.alerts)}>
                All
              </button>
            }
          >
            <AsyncContent query={alerts} isEmpty={(p) => p.items.length === 0} emptyTitle="No open alerts" compact>
              {(page) => (
                <DataTable
                  rows={page.items}
                  columns={alertColumns}
                  rowKey={(a) => a.alert_id}
                  dense
                  pageSize={8}
                  onRowClick={(a) => (a.order_id ? navigate(routes.orderDetail(encodeURIComponent(a.order_id))) : navigate(routes.alerts))}
                />
              )}
            </AsyncContent>
          </Section>
          <Section title="Unscheduled" count={schedule.data?.unscheduled.length} flush scroll style={{ maxHeight: 260 }}>
            <AsyncContent query={schedule} isEmpty={(s) => s.unscheduled.length === 0} emptyTitle="Everything is scheduled" compact>
              {(s) => (
                <DataTable
                  rows={s.unscheduled}
                  columns={unscheduledColumns}
                  rowKey={(u) => `${u.order_id}-${u.operation_id ?? ""}`}
                  onRowClick={(u) => navigate(routes.orderDetail(encodeURIComponent(u.order_id)))}
                  dense
                  pageSize={100}
                />
              )}
            </AsyncContent>
          </Section>
        </div>
      </div>
    </div>
  );
}
