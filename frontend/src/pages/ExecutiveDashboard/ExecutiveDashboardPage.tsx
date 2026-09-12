import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useBottlenecks, useKpis, useOnTimeDelivery, useScheduleQuality } from "@/api/analytics";
import { useAlerts } from "@/api/alerts";
import type { Bottleneck } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { RiskBadge, SeverityBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { axisTick, chartColors, tooltipStyle } from "@/lib/chartTheme";
import { formatCurrency, formatHours, formatNumber, formatPct, formatScore } from "@/lib/formatters";
import { formatDateTime } from "@/lib/time";

const bottleneckColumns: Column<Bottleneck>[] = [
  { key: "sev", header: "Sev", width: 80, cell: (b) => <RiskBadge level={b.severity} /> },
  { key: "name", header: "Resource", cell: (b) => <span className="strong">{b.resource_name}</span> },
  { key: "util", header: "Util", numeric: true, cell: (b) => formatPct(b.utilization_pct, 0), sortValue: (b) => b.utilization_pct },
  { key: "waiting", header: "Waiting", numeric: true, cell: (b) => b.orders_waiting, sortValue: (b) => b.orders_waiting },
  { key: "short", header: "Shortfall", numeric: true, cell: (b) => formatHours(b.capacity_shortfall_hours), sortValue: (b) => b.capacity_shortfall_hours },
  { key: "rev", header: "Rev at risk", numeric: true, cell: (b) => formatCurrency(b.revenue_at_risk), sortValue: (b) => b.revenue_at_risk },
];

/** Executive view: delivery health, money at risk, capacity and the top bottlenecks. */
export default function ExecutiveDashboardPage() {
  const navigate = useNavigate();
  const kpis = useKpis();
  const otd = useOnTimeDelivery({ bucket: "week" });
  const quality = useScheduleQuality();
  const bottlenecks = useBottlenecks();
  const alerts = useAlerts({ acknowledged: false, limit: 6 });

  const otdSeries = useMemo(
    () => (otd.data ?? []).map((p) => ({ period: formatDateTime(p.period_start, "dd MMM"), on_time_pct: p.on_time_pct ?? 0, late: p.late, on_time: p.on_time })),
    [otd.data],
  );
  const qualityComponents = useMemo(
    () => Object.entries(quality.data?.quality?.components ?? {}).map(([name, value]) => ({ name: name.replace(/_/g, " "), value })),
    [quality.data],
  );

  const k = kpis.data;
  const otdSpark = otdSeries.map((p) => p.on_time_pct);
  return (
    <div className="page">
      <PageHeader eyebrow="Analyse" title="Executive Dashboard" subtitle={k ? `As of ${formatDateTime(k.as_of)} · ${k.total_open_orders} open orders · ${formatNumber(k.total_pending_quantity)} units pending` : "Delivery performance, exposure and capacity at a glance"} />

      <div className="grid grid-kpi">
        <KpiCard label="Expected OTD" value={formatPct(k?.expected_on_time_delivery_pct, 0)} tone="ready" loading={kpis.isPending} hint={`Actual ${formatPct(k?.on_time_delivery_pct, 0)}`} sparkline={otdSpark.length > 1 ? otdSpark : undefined} />
        <KpiCard label="Revenue at risk" value={formatCurrency(k?.revenue_at_risk)} tone="late" loading={kpis.isPending} hint={`Margin at risk ${formatCurrency(k?.margin_at_risk)}`} />
        <KpiCard label="Overdue orders" value={k?.overdue_orders ?? "—"} tone="late" loading={kpis.isPending} onClick={() => navigate(`${routes.priorityQueue}?risk_level=critical`)} />
        <KpiCard label="At-risk orders" value={k?.at_risk_orders ?? "—"} tone="at-risk" loading={kpis.isPending} onClick={() => navigate(`${routes.priorityQueue}?risk_level=high`)} />
        <KpiCard label="Due this week" value={k?.orders_due_this_week ?? "—"} tone="neutral" loading={kpis.isPending} hint={`${k?.orders_due_today ?? "—"} today`} />
        <KpiCard label="Machine utilisation" value={formatPct(k?.machine_utilization_pct, 0)} tone="running" loading={kpis.isPending} hint={`Capacity ${formatPct(k?.capacity_utilization_pct, 0)}`} />
        <KpiCard label="Blocked orders" value={k?.blocked_total ?? "—"} tone="blocked" loading={kpis.isPending} hint={`Material ${k?.blocked_by_material ?? "—"} · Approval ${k?.waiting_for_approval ?? "—"}`} />
        <KpiCard label="Schedule quality" value={formatScore(quality.data?.quality?.score)} unit="/100" tone="running" loading={quality.isPending} hint={quality.data?.version ? `v${quality.data.version.version_number} · ${quality.data.version.status}` : undefined} onClick={() => navigate(routes.gantt)} />
      </div>

      <div className="grid grid-2">
        <Section title="On-time delivery trend">
          <AsyncContent query={otd} emptyTitle="No delivery history" compact>
            {() => (
              <div className="chart-box">
                <ResponsiveContainer>
                  <LineChart data={otdSeries} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
                    <CartesianGrid stroke={chartColors.grid} vertical={false} />
                    <XAxis dataKey="period" tick={axisTick} axisLine={false} tickLine={false} />
                    <YAxis domain={[0, 100]} tick={axisTick} axisLine={false} tickLine={false} unit="%" />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`${Number(v ?? 0).toFixed(1)}%`, "On time"]} />
                    <Line type="monotone" dataKey="on_time_pct" stroke={chartColors.ready} strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </AsyncContent>
        </Section>
        <Section title="Schedule quality components" actions={quality.data?.quality ? <span className="text-muted text-xs">{quality.data.quality.summary}</span> : null}>
          <AsyncContent query={quality} isEmpty={(q) => !q.quality} emptyTitle="No schedule yet" emptyMessage="Quality is computed for every generated schedule." compact>
            {() => (
              <div className="chart-box">
                <ResponsiveContainer>
                  <BarChart data={qualityComponents} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 24 }}>
                    <CartesianGrid stroke={chartColors.grid} horizontal={false} />
                    <XAxis type="number" domain={[0, 100]} tick={axisTick} axisLine={false} tickLine={false} />
                    <YAxis type="category" dataKey="name" tick={axisTick} axisLine={false} tickLine={false} width={110} />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v) => [Number(v ?? 0).toFixed(0), "score"]} />
                    <Bar dataKey="value" fill={chartColors.primary} radius={[0, 3, 3, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </AsyncContent>
        </Section>
      </div>

      <div className="grid grid-2">
        <Section title="Top bottlenecks" flush actions={<button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(routes.bottlenecks)}>Analysis</button>}>
          <AsyncContent query={bottlenecks} emptyTitle="No bottlenecks detected" compact>
            {(rows) => <DataTable rows={rows.slice(0, 6)} columns={bottleneckColumns} rowKey={(b) => `${b.resource_type}:${b.resource_id}`} dense pageSize={6} initialSort={{ key: "util", direction: "desc" }} />}
          </AsyncContent>
        </Section>
        <Section title="Critical alerts" count={alerts.data?.meta.total} flush actions={<button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(routes.alerts)}>All alerts</button>}>
          <AsyncContent query={alerts} isEmpty={(p) => p.items.length === 0} emptyTitle="No open alerts" compact>
            {(page) => (
              <DataTable
                rows={page.items}
                columns={[
                  { key: "sev", header: "Sev", width: 80, cell: (a) => <SeverityBadge severity={a.severity} /> },
                  { key: "title", header: "Alert", cell: (a) => a.title },
                  { key: "action", header: "Recommended action", cell: (a) => <span className="text-muted truncate" style={{ maxWidth: 280, display: "inline-block" }} title={a.recommended_action}>{a.recommended_action}</span> },
                ]}
                rowKey={(a) => a.alert_id}
                dense
                pageSize={6}
              />
            )}
          </AsyncContent>
        </Section>
      </div>
    </div>
  );
}
