/**
 * Bottleneck Analysis (spec Phase 12): the current bottleneck in the spec's exact format, a ranked table
 * with severity and recommendation, utilisation bars per machine group / machine / process and a
 * drill-down to the affected orders (GET /analytics/bottlenecks + GET /orders).
 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useBottlenecks } from "@/api/analytics";
import { useOrders } from "@/api/orders";
import type { Bottleneck, OrderListItem, OrderListQuery, ProcessType } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { BottleneckCard } from "@/components/BottleneckCard";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { RiskBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { Tabs } from "@/components/Tabs";
import { axisTick, chartColors, tooltipStyle, utilizationColor } from "@/lib/chartTheme";
import { RISK_RANK, humanize } from "@/lib/constants";
import { formatCurrency, formatNumber, formatPct } from "@/lib/formatters";
import { formatWorkHours } from "@/lib/metricPairs";
import { formatDateTime, formatRelative } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

import { customerColumn, dueColumn, latenessColumn, machineColumn, orderIdColumn, orderRowClass, partColumn, readinessColumn, riskColumn, scoreColumn, valueColumn } from "../shared/orderColumns";

const FILTER_KEYS = ["type", "selected"] as const;
type ResourceType = Bottleneck["resource_type"];
const RESOURCE_TYPES: ResourceType[] = ["machine_group", "machine", "process", "material", "tooling"];

const bottleneckKey = (b: Bottleneck) => `${b.resource_type}:${b.resource_id}`;

const columns: Column<Bottleneck>[] = [
  { key: "rank", header: "#", width: 40, numeric: true, cell: () => null, sortValue: (b) => RISK_RANK[b.severity] * 1e12 + b.revenue_at_risk },
  { key: "sev", header: "Severity", width: 90, cell: (b) => <RiskBadge level={b.severity} />, sortValue: (b) => RISK_RANK[b.severity], filterValue: (b) => b.severity },
  { key: "type", header: "Type", cell: (b) => humanize(b.resource_type), sortValue: (b) => b.resource_type, filterValue: (b) => b.resource_type },
  { key: "name", header: "Resource", cell: (b) => <span className="strong">{b.resource_name}</span>, sortValue: (b) => b.resource_name, filterValue: (b) => `${b.resource_id} ${b.resource_name}` },
  { key: "util", header: "Utilisation", numeric: true, cell: (b) => <span className={b.utilization_pct >= 100 ? "tone-late strong" : b.utilization_pct >= 85 ? "tone-at-risk" : ""}>{formatPct(b.utilization_pct, 0)}</span>, sortValue: (b) => b.utilization_pct },
  { key: "waiting", header: "Orders waiting", numeric: true, cell: (b) => formatNumber(b.orders_waiting), sortValue: (b) => b.orders_waiting },
  { key: "short", header: "Shortfall", numeric: true, cell: (b) => formatWorkHours(b.capacity_shortfall_hours, 0), sortValue: (b) => b.capacity_shortfall_hours },
  { key: "rev", header: "Revenue at risk", numeric: true, cell: (b) => formatCurrency(b.revenue_at_risk), sortValue: (b) => b.revenue_at_risk },
  { key: "margin", header: "Margin at risk", numeric: true, cell: (b) => formatCurrency(b.margin_at_risk), sortValue: (b) => b.margin_at_risk },
  { key: "rec", header: "Recommendation", cell: (b) => <span className="text-muted truncate" style={{ maxWidth: 360, display: "inline-block" }} title={b.recommendation}>{b.recommendation}</span> },
];

/** GET /orders filter that reaches the orders queued on the bottleneck resource (tooling has no filter). */
function affectedQuery(b: Bottleneck): OrderListQuery | null {
  switch (b.resource_type) {
    case "machine_group":
      return { machine_group: b.resource_id };
    case "machine":
      return { machine_id: b.resource_id };
    case "process":
      return { process_type: b.resource_id as ProcessType };
    case "material":
      return { search: b.resource_id };
    default:
      return null;
  }
}

export default function BottleneckAnalysisPage() {
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const { values, set } = useSearchState(FILTER_KEYS);
  const bottlenecks = useBottlenecks();
  const [page, setPage] = useState(1);

  const items = useMemo(() => bottlenecks.data?.items ?? [], [bottlenecks.data]);
  const ranked = useMemo(() => [...items].sort((a, b) => RISK_RANK[b.severity] - RISK_RANK[a.severity] || b.revenue_at_risk - a.revenue_at_risk), [items]);
  const rankOf = useMemo(() => new Map(ranked.map((b, i) => [bottleneckKey(b), i + 1])), [ranked]);
  const typeCounts = useMemo(() => Object.fromEntries(RESOURCE_TYPES.map((t) => [t, items.filter((b) => b.resource_type === t).length])) as Record<ResourceType, number>, [items]);
  const chartType: ResourceType = RESOURCE_TYPES.includes(values.type as ResourceType) ? (values.type as ResourceType) : (RESOURCE_TYPES.find((t) => typeCounts[t] > 0) ?? "machine_group");
  const chart = useMemo(
    () =>
      items
        .filter((b) => b.resource_type === chartType)
        .sort((a, b) => b.utilization_pct - a.utilization_pct)
        .slice(0, 30)
        .map((b) => ({ name: b.resource_name, util: Math.round(b.utilization_pct), key: bottleneckKey(b), waiting: b.orders_waiting })),
    [items, chartType],
  );
  const selected = useMemo(() => ranked.find((b) => bottleneckKey(b) === values.selected) ?? null, [ranked, values.selected]);
  const totals = useMemo(
    () => ({
      critical: items.filter((b) => b.severity === "critical").length,
      high: items.filter((b) => b.severity === "high").length,
      revenue: items.reduce((s, b) => s + b.revenue_at_risk, 0),
      shortfall: items.reduce((s, b) => s + b.capacity_shortfall_hours, 0),
      waiting: items.reduce((s, b) => s + b.orders_waiting, 0),
    }),
    [items],
  );

  const drillQuery = selected ? affectedQuery(selected) : null;
  const affected = useOrders({ ...(drillQuery ?? {}), sort: "risk", order: "desc", page, page_size: 25 }, drillQuery !== null);
  const rankColumn: Column<Bottleneck> = { ...columns[0]!, cell: (b) => rankOf.get(bottleneckKey(b)) ?? "—" };
  const tableColumns = [rankColumn, ...columns.slice(1)];
  const orderColumns = useMemo<Column<OrderListItem>[]>(() => [orderIdColumn(), customerColumn(), partColumn(), dueColumn(now), machineColumn(), scoreColumn(), riskColumn(), readinessColumn(), latenessColumn(), valueColumn()], [now]);

  const select = (b: Bottleneck | null) => {
    setPage(1);
    set("selected", b ? bottleneckKey(b) : "");
  };

  return (
    <div className="page" data-testid="bottleneck-analysis">
      <PageHeader
        eyebrow="Analyse"
        title="Bottleneck Analysis"
        subtitle={
          bottlenecks.data ? (
            <span>
              {bottlenecks.data.source === "stored" ? "Computed with" : "Recomputed live against"} plan {bottlenecks.data.version_number !== null ? `v${bottlenecks.data.version_number} (${humanize(bottlenecks.data.version_status)})` : "—"} · as of {formatDateTime(bottlenecks.data.as_of)} ({formatRelative(bottlenecks.data.as_of, now)}). Where additional capacity is needed, ranked by exposure.
            </span>
          ) : (
            "Resources whose utilisation or queue threatens delivery, ranked by exposure."
          )
        }
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => void bottlenecks.refetch()} disabled={bottlenecks.isFetching}>
              {bottlenecks.isFetching ? "Refreshing…" : "Refresh"}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => navigate(routes.capacity)}>
              Capacity planning
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={() => navigate(routes.simulation)}>
              Simulate extra capacity
            </button>
          </>
        }
      />

      <div className="grid grid-main-side">
        <AsyncContent query={bottlenecks} emptyTitle="No bottleneck data" compact isEmpty={() => false}>
          {(r) => <BottleneckCard bottleneck={r.current} onOpen={(b) => select(b)} openLabel="Show affected orders" />}
        </AsyncContent>
        <div className="grid grid-kpi">
          <KpiCard label="Bottlenecks" value={formatNumber(items.length)} tone="neutral" loading={bottlenecks.isPending} hint={`${totals.critical} critical · ${totals.high} high`} />
          <KpiCard label="Orders waiting" value={formatNumber(totals.waiting)} tone={totals.waiting > 0 ? "blocked" : "ready"} loading={bottlenecks.isPending} hint="across every bottleneck" />
          <KpiCard label="Capacity shortfall" value={formatWorkHours(totals.shortfall, 0)} tone={totals.shortfall > 0 ? "at-risk" : "ready"} loading={bottlenecks.isPending} hint="machine hours" />
          <KpiCard label="Revenue at risk" value={formatCurrency(totals.revenue)} tone={totals.revenue > 0 ? "late" : "ready"} loading={bottlenecks.isPending} />
        </div>
      </div>

      <Section
        title="Utilisation"
        count={`${chart.length} ${humanize(chartType).toLowerCase()}${chart.length === 1 ? "" : "s"}`}
        actions={<span className="text-faint text-xs">click a bar to drill down · dashed line = 100%</span>}
      >
        <Tabs
          ariaLabel="Resource type"
          value={chartType}
          onChange={(t) => set("type", t)}
          items={RESOURCE_TYPES.map((t) => ({ key: t, label: humanize(t), count: typeCounts[t] }))}
        />
        <AsyncContent query={bottlenecks} isEmpty={() => chart.length === 0} emptyTitle="No bottlenecks of this type" emptyMessage="Nothing of this resource type is overloaded in the plan." compact>
          {() => (
            <div className="chart-box mt-2" style={{ height: Math.max(160, chart.length * 24 + 40) }}>
              <ResponsiveContainer>
                <BarChart data={chart} layout="vertical" margin={{ top: 4, right: 32, bottom: 0, left: 24 }} barSize={12}>
                  <CartesianGrid stroke={chartColors.grid} horizontal={false} />
                  <XAxis type="number" domain={[0, (max: number) => Math.max(100, Math.ceil(max / 25) * 25)]} tick={axisTick} axisLine={false} tickLine={false} unit="%" />
                  <YAxis type="category" dataKey="name" tick={axisTick} axisLine={false} tickLine={false} width={150} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v, _n, item) => [`${Number(v ?? 0).toFixed(0)}% · ${(item.payload as { waiting: number }).waiting} waiting`, "utilisation"]} />
                  <ReferenceLine x={100} stroke={chartColors.late} strokeDasharray="4 3" />
                  <Bar dataKey="util" radius={[0, 3, 3, 0]} isAnimationActive={false} onClick={(_d, index) => { const c = chart[index]; if (c) set("selected", c.key); }}>
                    {chart.map((c) => (
                      <Cell key={c.key} fill={utilizationColor(c.util)} cursor="pointer" stroke={c.key === values.selected ? "var(--fg)" : undefined} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </AsyncContent>
      </Section>

      <Section title="Ranked bottlenecks" count={items.length} flush>
        <AsyncContent query={bottlenecks} isEmpty={(r) => r.items.length === 0} emptyTitle="No bottlenecks detected" emptyMessage="Generate a schedule; bottlenecks are derived from the planned load." compact>
          {() => (
            <DataTable
              rows={ranked}
              columns={tableColumns}
              rowKey={bottleneckKey}
              selectedKey={values.selected || null}
              onRowClick={(b) => select(bottleneckKey(b) === values.selected ? null : b)}
              rowClassName={(b) => (b.severity === "critical" ? "row-late" : b.severity === "high" ? "row-at-risk" : undefined)}
              initialSort={{ key: "rank", direction: "desc" }}
              filters
              dense
              pageSize={50}
              ariaLabel="Bottlenecks"
            />
          )}
        </AsyncContent>
      </Section>

      <Section
        title={selected ? `Affected orders · ${selected.resource_name}` : "Affected orders"}
        count={affected.data ? `${formatNumber(affected.data.meta.total)} open orders · highest risk first` : undefined}
        flush
        actions={
          selected ? (
            <>
              <RiskBadge level={selected.severity} />
              <span className="text-muted text-xs">{selected.recommendation}</span>
              {selected.resource_type === "machine" ? (
                <button type="button" className="btn btn-sm" onClick={() => navigate(routes.machineDetail(encodeURIComponent(selected.resource_id)))}>
                  Machine detail
                </button>
              ) : null}
              {drillQuery ? (
                <button type="button" className="btn btn-sm" onClick={() => navigate(`${routes.priorityQueue}?${new URLSearchParams(Object.entries(drillQuery).map(([k, v]) => [k, String(v)])).toString()}`)}>
                  Open in queue
                </button>
              ) : null}
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => select(null)}>
                Clear
              </button>
            </>
          ) : null
        }
      >
        {!selected ? (
          <EmptyState compact title="Select a bottleneck" message="Click a bar or a table row to list the orders waiting on that resource." />
        ) : drillQuery === null ? (
          <EmptyState compact title="No order filter for tooling bottlenecks" message="GET /orders cannot filter by tooling id; open the priority queue and search for the tool instead." />
        ) : (
          <AsyncContent query={affected} isEmpty={(p) => p.items.length === 0} emptyTitle="No open orders on this resource" compact>
            {(p) => (
              <>
                <DataTable rows={p.items} columns={orderColumns} rowKey={(o) => o.order_id} onRowClick={(o) => navigate(routes.orderDetail(encodeURIComponent(o.order_id)))} rowClassName={(o) => orderRowClass(o, now)} paginate={false} hideFooter dense ariaLabel="Affected orders" />
                {p.meta.total > p.items.length ? (
                  <div className="row" style={{ padding: "var(--sp-2) var(--sp-3)" }}>
                    <span className="text-muted text-xs">
                      page {page} · showing {p.items.length} of {formatNumber(p.meta.total)}
                    </span>
                    <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => setPage((x) => x - 1)}>
                      ‹
                    </button>
                    <button type="button" className="btn btn-sm" disabled={!p.meta.has_more} onClick={() => setPage((x) => x + 1)}>
                      ›
                    </button>
                  </div>
                ) : null}
              </>
            )}
          </AsyncContent>
        )}
      </Section>
    </div>
  );
}
