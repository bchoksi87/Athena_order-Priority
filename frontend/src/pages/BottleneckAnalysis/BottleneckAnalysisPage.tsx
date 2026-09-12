import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useBottlenecks } from "@/api/analytics";
import type { Bottleneck } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { RiskBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { axisTick, chartColors, tooltipStyle, utilizationColor } from "@/lib/chartTheme";
import { humanize } from "@/lib/constants";
import { formatCurrency, formatHours, formatPct } from "@/lib/formatters";

const columns: Column<Bottleneck>[] = [
  { key: "sev", header: "Severity", width: 90, cell: (b) => <RiskBadge level={b.severity} />, sortValue: (b) => ({ critical: 4, high: 3, medium: 2, low: 1 })[b.severity] },
  { key: "type", header: "Type", cell: (b) => humanize(b.resource_type), sortValue: (b) => b.resource_type, filterValue: (b) => b.resource_type },
  { key: "name", header: "Resource", cell: (b) => <span className="strong">{b.resource_name}</span>, sortValue: (b) => b.resource_name, filterValue: (b) => `${b.resource_id} ${b.resource_name}` },
  { key: "util", header: "Utilisation", numeric: true, cell: (b) => <span className={b.utilization_pct >= 95 ? "tone-late strong" : b.utilization_pct >= 85 ? "tone-at-risk" : ""}>{formatPct(b.utilization_pct, 0)}</span>, sortValue: (b) => b.utilization_pct },
  { key: "waiting", header: "Orders waiting", numeric: true, cell: (b) => b.orders_waiting, sortValue: (b) => b.orders_waiting },
  { key: "short", header: "Shortfall", numeric: true, cell: (b) => formatHours(b.capacity_shortfall_hours), sortValue: (b) => b.capacity_shortfall_hours },
  { key: "rev", header: "Revenue at risk", numeric: true, cell: (b) => formatCurrency(b.revenue_at_risk), sortValue: (b) => b.revenue_at_risk },
  { key: "margin", header: "Margin at risk", numeric: true, cell: (b) => formatCurrency(b.margin_at_risk), sortValue: (b) => b.margin_at_risk },
  { key: "rec", header: "Recommendation", cell: (b) => <span className="text-muted">{b.recommendation}</span> },
];

/** Where capacity is short, what it costs, and what to do about it. */
export default function BottleneckAnalysisPage() {
  const navigate = useNavigate();
  const bottlenecks = useBottlenecks();
  const [selected, setSelected] = useState<string | null>(null);

  const rows = useMemo(() => bottlenecks.data ?? [], [bottlenecks.data]);
  const chart = useMemo(() => [...rows].sort((a, b) => b.utilization_pct - a.utilization_pct).slice(0, 20).map((b) => ({ name: b.resource_name, util: b.utilization_pct, id: `${b.resource_type}:${b.resource_id}` })), [rows]);
  const totals = useMemo(
    () => ({
      critical: rows.filter((b) => b.severity === "critical").length,
      high: rows.filter((b) => b.severity === "high").length,
      revenue: rows.reduce((s, b) => s + b.revenue_at_risk, 0),
      shortfall: rows.reduce((s, b) => s + b.capacity_shortfall_hours, 0),
      waiting: rows.reduce((s, b) => s + b.orders_waiting, 0),
    }),
    [rows],
  );
  const selectedRow = rows.find((b) => `${b.resource_type}:${b.resource_id}` === selected) ?? null;

  return (
    <div className="page">
      <PageHeader eyebrow="Analyse" title="Bottleneck Analysis" subtitle="Resources whose utilisation or queue threatens delivery, ranked by exposure." actions={<button type="button" className="btn" onClick={() => navigate(routes.capacity)}>Capacity planning</button>} />
      <div className="grid grid-kpi">
        <KpiCard label="Bottlenecks" value={rows.length} tone="neutral" loading={bottlenecks.isPending} />
        <KpiCard label="Critical" value={totals.critical} tone="late" loading={bottlenecks.isPending} hint={`${totals.high} high`} />
        <KpiCard label="Orders waiting" value={totals.waiting} tone="blocked" loading={bottlenecks.isPending} />
        <KpiCard label="Capacity shortfall" value={formatHours(totals.shortfall)} tone="at-risk" loading={bottlenecks.isPending} />
        <KpiCard label="Revenue at risk" value={formatCurrency(totals.revenue)} tone="late" loading={bottlenecks.isPending} />
      </div>
      <Section title="Utilisation by resource" count="top 20">
        <AsyncContent query={bottlenecks} emptyTitle="No bottlenecks" emptyMessage="Generate a schedule; bottlenecks are derived from the planned load." compact>
          {() => (
            <div className="chart-box" style={{ height: Math.max(200, chart.length * 22 + 40) }}>
              <ResponsiveContainer>
                <BarChart data={chart} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 24 }}>
                  <CartesianGrid stroke={chartColors.grid} horizontal={false} />
                  <XAxis type="number" domain={[0, (max: number) => Math.max(100, Math.ceil(max / 10) * 10)]} tick={axisTick} axisLine={false} tickLine={false} unit="%" />
                  <YAxis type="category" dataKey="name" tick={axisTick} axisLine={false} tickLine={false} width={140} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`${Number(v ?? 0).toFixed(0)}%`, "utilisation"]} />
                  <ReferenceLine x={100} stroke={chartColors.late} strokeDasharray="4 3" />
                  <Bar dataKey="util" radius={[0, 3, 3, 0]} isAnimationActive={false} onClick={(d: { id?: string }) => setSelected(d.id ?? null)}>
                    {chart.map((c) => (
                      <Cell key={c.id} fill={utilizationColor(c.util)} cursor="pointer" />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </AsyncContent>
      </Section>
      <div className="grid grid-main-side">
        <Section title="Bottlenecks" count={rows.length} flush>
          <AsyncContent query={bottlenecks} emptyTitle="No bottlenecks" compact>
            {(data) => <DataTable rows={data} columns={columns} rowKey={(b) => `${b.resource_type}:${b.resource_id}`} selectedKey={selected} onRowClick={(b) => setSelected(`${b.resource_type}:${b.resource_id}`)} rowClassName={(b) => (b.severity === "critical" ? "row-late" : b.severity === "high" ? "row-at-risk" : undefined)} initialSort={{ key: "sev", direction: "desc" }} filters dense pageSize={50} />}
          </AsyncContent>
        </Section>
        <Section title={selectedRow ? selectedRow.resource_name : "Detail"} actions={selectedRow && selectedRow.resource_type === "machine" ? <button type="button" className="btn btn-sm" onClick={() => navigate(routes.machineDetail(encodeURIComponent(selectedRow.resource_id)))}>Open machine</button> : null}>
          {selectedRow ? (
            <dl className="kv">
              <dt>Type</dt>
              <dd>{humanize(selectedRow.resource_type)}</dd>
              <dt>Severity</dt>
              <dd><RiskBadge level={selectedRow.severity} /></dd>
              <dt>Utilisation</dt>
              <dd className="num">{formatPct(selectedRow.utilization_pct)}</dd>
              <dt>Orders waiting</dt>
              <dd className="num">{selectedRow.orders_waiting}</dd>
              <dt>Shortfall</dt>
              <dd className="num">{formatHours(selectedRow.capacity_shortfall_hours)}</dd>
              <dt>Revenue at risk</dt>
              <dd className="num">{formatCurrency(selectedRow.revenue_at_risk)}</dd>
              <dt>Margin at risk</dt>
              <dd className="num">{formatCurrency(selectedRow.margin_at_risk)}</dd>
              <dt>Recommendation</dt>
              <dd>{selectedRow.recommendation}</dd>
            </dl>
          ) : (
            <span className="text-muted text-sm">Select a bar or a row to see the recommendation.</span>
          )}
        </Section>
      </div>
    </div>
  );
}
