/**
 * Capacity Planning (spec Phase 13): required vs available hours by day / week and by machine,
 * machine group, process or department — the spec's "Process | Required Hrs | Available Hrs | Gap"
 * table, a grouped bar chart per period, gap highlighting, a horizon selector and the engine's
 * unallocated / unknown-hours notes (GET /analytics/capacity).
 */
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useCapacity } from "@/api/analytics";
import type { CapacityDimension, CapacityPeriod, CapacityPeriodRow, CapacityTotals } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { Toolbar, ToolbarGroup, ToolbarSpacer } from "@/components/Toolbar";
import { axisTick, chartColors, tooltipStyle } from "@/lib/chartTheme";
import { humanize } from "@/lib/constants";
import { formatNumber, formatPct } from "@/lib/formatters";
import { formatWorkHours } from "@/lib/metricPairs";
import { formatDateTime } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["dimension", "period", "horizon", "key"] as const;
const DIMENSIONS: CapacityDimension[] = ["machine_group", "machine", "process", "department"];
const PERIODS: CapacityPeriod[] = ["day", "week"];
const HORIZONS = [7, 14, 28, 56, 84, 120] as const;
const PLURAL: Record<CapacityDimension, string> = { machine: "machines", machine_group: "machine groups", process: "processes", department: "departments" };

function gapCell(gap: number): React.ReactNode {
  const text = `${gap > 0 ? "+" : gap < 0 ? "−" : ""}${formatWorkHours(Math.abs(gap), 0)}`;
  return <span className={gap < 0 ? "tone-late strong" : gap > 0 ? "tone-ready" : "text-muted"}>{text}</span>;
}

function loadCell(pct: number): React.ReactNode {
  return <span className={pct > 100 ? "tone-late strong" : pct >= 85 ? "tone-at-risk" : ""}>{formatPct(pct, 0)}</span>;
}

const totalsColumns = (dimension: string): Column<CapacityTotals>[] => [
  { key: "key", header: humanize(dimension), cell: (r) => <span className="strong">{humanize(r.key)}</span>, sortValue: (r) => r.key, filterValue: (r) => r.key },
  { key: "required", header: "Required hrs", numeric: true, cell: (r) => formatWorkHours(r.required_hours, 0), sortValue: (r) => r.required_hours },
  { key: "available", header: "Available hrs", numeric: true, cell: (r) => formatWorkHours(r.available_hours, 0), sortValue: (r) => r.available_hours },
  { key: "gap", header: "Gap", numeric: true, cell: (r) => gapCell(r.gap_hours), sortValue: (r) => r.gap_hours, title: "Available − required; negative = shortfall (overtime, outsourcing or re-prioritisation)" },
  { key: "load", header: "Load", numeric: true, cell: (r) => loadCell(r.utilization_pct), sortValue: (r) => r.utilization_pct },
  { key: "scheduled", header: "Scheduled", numeric: true, cell: (r) => formatWorkHours(r.scheduled_hours, 0), sortValue: (r) => r.scheduled_hours, title: "Hours placed by the scheduler inside the horizon" },
  { key: "estimated", header: "Estimated", numeric: true, cell: (r) => formatWorkHours(r.estimated_hours, 0), sortValue: (r) => r.estimated_hours, title: "Pending work estimated from routing, not yet placed" },
  { key: "shortfall", header: "Shortfall", numeric: true, cell: (r) => <span className={r.shortfall_hours > 0 ? "tone-late" : "text-muted"}>{formatWorkHours(r.shortfall_hours, 0)}</span>, sortValue: (r) => r.shortfall_hours },
];

const rowColumns = (dimension: string): Column<CapacityPeriodRow>[] => [
  { key: "key", header: humanize(dimension), cell: (r) => <span className="strong">{humanize(r.key)}</span>, sortValue: (r) => r.key, filterValue: (r) => r.key },
  { key: "period", header: "Period", cell: (r) => <span className="num text-nowrap">{formatDateTime(r.period_start, "dd MMM")} – {formatDateTime(r.period_end, "dd MMM")}</span>, sortValue: (r) => r.period_start, filterValue: (r) => formatDateTime(r.period_start, "dd MMM") },
  { key: "required", header: "Required hrs", numeric: true, cell: (r) => formatWorkHours(r.required_hours, 0), sortValue: (r) => r.required_hours },
  { key: "available", header: "Available hrs", numeric: true, cell: (r) => formatWorkHours(r.available_hours, 0), sortValue: (r) => r.available_hours },
  { key: "gap", header: "Gap", numeric: true, cell: (r) => gapCell(r.gap_hours), sortValue: (r) => r.gap_hours },
  { key: "load", header: "Load", numeric: true, cell: (r) => loadCell(r.utilization_pct), sortValue: (r) => r.utilization_pct },
];

export default function CapacityPlanningPage() {
  const navigate = useNavigate();
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const dimension: CapacityDimension = DIMENSIONS.includes(values.dimension as CapacityDimension) ? (values.dimension as CapacityDimension) : "machine_group";
  const period: CapacityPeriod = PERIODS.includes(values.period as CapacityPeriod) ? (values.period as CapacityPeriod) : "week";
  const horizon = values.horizon ? Number(values.horizon) : undefined;
  const capacity = useCapacity({ dimension, period, horizon_days: horizon });
  const report = capacity.data;
  const selectedKey = values.key;

  const perPeriod = useMemo(() => {
    const rows = (report?.rows ?? []).filter((r) => !selectedKey || r.key === selectedKey);
    const acc = new Map<string, { period: string; label: string; required: number; available: number }>();
    for (const r of rows) {
      const cur = acc.get(r.period_start) ?? { period: r.period_start, label: period === "day" ? formatDateTime(r.period_start, "EEE dd MMM") : `${formatDateTime(r.period_start, "dd MMM")} – ${formatDateTime(r.period_end, "dd MMM")}`, required: 0, available: 0 };
      cur.required += r.required_hours;
      cur.available += r.available_hours;
      acc.set(r.period_start, cur);
    }
    return Array.from(acc.values())
      .sort((a, b) => a.period.localeCompare(b.period))
      .map((p) => ({ ...p, gap: p.available - p.required }));
  }, [report, selectedKey, period]);

  const byResource = useMemo(() => [...(report?.totals ?? [])].sort((a, b) => a.gap_hours - b.gap_hours).map((t) => ({ name: humanize(t.key), key: t.key, required: t.required_hours, available: t.available_hours, gap: t.gap_hours })), [report]);
  const overloaded = useMemo(() => (report?.totals ?? []).filter((t) => t.gap_hours < 0), [report]);
  const periodRows = useMemo(() => (report?.rows ?? []).filter((r) => !selectedKey || r.key === selectedKey), [report, selectedKey]);

  return (
    <div className="page" data-testid="capacity-planning">
      <PageHeader
        eyebrow="Analyse"
        title="Capacity Planning"
        subtitle={
          report ? (
            <span>
              Required vs available hours by {humanize(report.dimension).toLowerCase()} per {report.period}, {formatDateTime(report.horizon_start, "dd MMM")} – {formatDateTime(report.horizon_end, "dd MMM yyyy")}. Negative gaps mean overtime, outsourcing or re-prioritisation.
            </span>
          ) : (
            "Planned load against available working hours over the scheduling horizon."
          )
        }
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => navigate(routes.bottlenecks)}>
              Bottlenecks
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={() => navigate(routes.simulation)}>
              Simulate extra shift / machine
            </button>
          </>
        }
      />

      <Toolbar>
        <ToolbarGroup>
          <span className="label" style={{ marginBottom: 0 }}>
            By
          </span>
          {DIMENSIONS.map((d) => (
            <button key={d} type="button" className={`btn btn-sm${dimension === d ? " btn-primary" : ""}`} onClick={() => { set("dimension", d === "machine_group" ? "" : d); set("key", ""); }} aria-pressed={dimension === d}>
              {humanize(d)}
            </button>
          ))}
        </ToolbarGroup>
        <ToolbarGroup>
          <span className="label" style={{ marginBottom: 0 }}>
            Per
          </span>
          {PERIODS.map((p) => (
            <button key={p} type="button" className={`btn btn-sm${period === p ? " btn-primary" : ""}`} onClick={() => set("period", p === "week" ? "" : p)} aria-pressed={period === p}>
              {p}
            </button>
          ))}
        </ToolbarGroup>
        <ToolbarGroup>
          <span className="label" style={{ marginBottom: 0 }}>
            Horizon
          </span>
          <select className="select" value={values.horizon} onChange={(e) => set("horizon", e.target.value)} aria-label="Horizon">
            <option value="">scheduling horizon</option>
            {HORIZONS.map((h) => (
              <option key={h} value={h}>
                {h} days
              </option>
            ))}
          </select>
        </ToolbarGroup>
        {selectedKey ? (
          <ToolbarGroup>
            <span className="chip active">{humanize(selectedKey)}</span>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => set("key", "")}>
              all {PLURAL[dimension]}
            </button>
          </ToolbarGroup>
        ) : null}
        <ToolbarSpacer />
        <button type="button" className="btn btn-sm btn-ghost" onClick={reset} disabled={!values.dimension && !values.period && !values.horizon && !values.key}>
          Reset
        </button>
      </Toolbar>

      <div className="grid grid-kpi" data-testid="capacity-kpis">
        <KpiCard label="Required hours" value={formatWorkHours(report?.total_required_hours, 0)} tone="neutral" loading={capacity.isPending} hint={report ? `${formatWorkHours(report.scheduled_hours, 0)} scheduled + ${formatWorkHours(report.estimated_hours, 0)} estimated` : undefined} />
        <KpiCard label="Available hours" value={formatWorkHours(report?.total_available_hours, 0)} tone="neutral" loading={capacity.isPending} hint="calendar working hours in the horizon" />
        <KpiCard label="Net gap" value={report ? `${report.gap_hours > 0 ? "+" : report.gap_hours < 0 ? "−" : ""}${formatWorkHours(Math.abs(report.gap_hours), 0)}` : "—"} tone={(report?.gap_hours ?? 0) < 0 ? "late" : "ready"} loading={capacity.isPending} hint="available − required" />
        <KpiCard label="Overall load" value={formatPct(report?.utilization_pct, 0)} tone={(report?.utilization_pct ?? 0) > 100 ? "late" : (report?.utilization_pct ?? 0) >= 85 ? "at-risk" : "running"} loading={capacity.isPending} />
        <KpiCard label="Overloaded" value={formatNumber(overloaded.length)} unit={overloaded.length === 1 ? humanize(dimension).toLowerCase() : PLURAL[dimension]} tone={overloaded.length > 0 ? "blocked" : "ready"} loading={capacity.isPending} hint={overloaded.length ? `${formatWorkHours(overloaded.reduce((s, t) => s + t.shortfall_hours, 0), 0)} shortfall` : "everything fits"} />
        <KpiCard label="Unallocated" value={formatWorkHours(report?.unallocated_hours, 0)} tone={(report?.unallocated_hours ?? 0) > 0 ? "at-risk" : "neutral"} loading={capacity.isPending} hint={report ? `${formatNumber(report.unallocated_operations)} operations without a ${humanize(dimension).toLowerCase()}` : undefined} />
      </div>

      <div className="grid grid-2">
        <Section title={`Required vs available per ${period}`} count={selectedKey ? humanize(selectedKey) : `all ${PLURAL[dimension]}`}>
          <AsyncContent query={capacity} isEmpty={() => perPeriod.length === 0} emptyTitle="No capacity data" emptyMessage="Generate a schedule to compute planned load." compact>
            {() => (
              <div className="chart-box" style={{ height: 260 }}>
                <ResponsiveContainer>
                  <BarChart data={perPeriod} margin={{ top: 8, right: 12, bottom: 0, left: -8 }} barGap={2} barCategoryGap="25%">
                    <CartesianGrid stroke={chartColors.grid} vertical={false} />
                    <XAxis dataKey="label" tick={axisTick} axisLine={false} tickLine={false} interval={0} angle={perPeriod.length > 8 ? -30 : 0} height={perPeriod.length > 8 ? 56 : 30} textAnchor={perPeriod.length > 8 ? "end" : "middle"} />
                    <YAxis tick={axisTick} axisLine={false} tickLine={false} unit="h" />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v, name) => [`${Number(v ?? 0).toFixed(0)} h`, name === "required" ? "Required" : "Available"]} />
                    <Legend wrapperStyle={{ fontSize: 11 }} formatter={(v) => (v === "required" ? "Required" : "Available")} />
                    <Bar dataKey="available" fill={chartColors.neutral} radius={[3, 3, 0, 0]} isAnimationActive={false} />
                    <Bar dataKey="required" fill={chartColors.primary} radius={[3, 3, 0, 0]} isAnimationActive={false}>
                      {perPeriod.map((p) => (
                        <Cell key={p.period} fill={p.gap < 0 ? chartColors.late : chartColors.primary} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </AsyncContent>
        </Section>
        <Section title={`Gap by ${humanize(dimension).toLowerCase()}`} count="horizon total" actions={<span className="text-faint text-xs">click a bar to focus</span>}>
          <AsyncContent query={capacity} isEmpty={() => byResource.length === 0} emptyTitle="No rows" compact>
            {() => (
              <div className="chart-box" style={{ height: Math.max(220, byResource.length * 22 + 40) }}>
                <ResponsiveContainer>
                  <BarChart data={byResource} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 24 }} barSize={12}>
                    <CartesianGrid stroke={chartColors.grid} horizontal={false} />
                    <XAxis type="number" tick={axisTick} axisLine={false} tickLine={false} unit="h" />
                    <YAxis type="category" dataKey="name" tick={axisTick} axisLine={false} tickLine={false} width={130} />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v, _n, item) => { const p = item.payload as { required: number; available: number }; return [`${Number(v ?? 0) > 0 ? "+" : ""}${Number(v ?? 0).toFixed(0)} h (required ${p.required.toFixed(0)} / available ${p.available.toFixed(0)})`, "gap"]; }} />
                    <ReferenceLine x={0} stroke={chartColors.axis} />
                    <Bar dataKey="gap" radius={[0, 3, 3, 0]} isAnimationActive={false} onClick={(_d, index) => { const r = byResource[index]; if (r) set("key", r.key === selectedKey ? "" : r.key); }}>
                      {byResource.map((r) => (
                        <Cell key={r.key} fill={r.gap < 0 ? chartColors.late : chartColors.ready} cursor="pointer" stroke={r.key === selectedKey ? "var(--fg)" : undefined} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </AsyncContent>
        </Section>
      </div>

      <Section title={`${humanize(dimension)} · Required Hrs · Available Hrs · Gap`} count={report ? `${report.totals.length} rows · horizon total` : undefined} flush>
        <AsyncContent query={capacity} isEmpty={(r) => r.totals.length === 0} emptyTitle="No capacity rows" emptyMessage="Generate a schedule to compute planned load." compact>
          {(r) => (
            <DataTable
              rows={r.totals}
              columns={totalsColumns(r.dimension)}
              rowKey={(t) => t.key}
              selectedKey={selectedKey || null}
              onRowClick={(t) => set("key", t.key === selectedKey ? "" : t.key)}
              rowClassName={(t) => (t.gap_hours < 0 ? "row-late" : t.utilization_pct >= 85 ? "row-at-risk" : undefined)}
              initialSort={{ key: "gap", direction: "asc" }}
              filters
              dense
              pageSize={100}
              ariaLabel="Capacity totals"
            />
          )}
        </AsyncContent>
      </Section>

      <Section title={`Per ${period}`} count={periodRows.length} flush>
        <AsyncContent query={capacity} isEmpty={() => periodRows.length === 0} emptyTitle="No period rows" compact>
          {(r) => <DataTable rows={periodRows} columns={rowColumns(r.dimension)} rowKey={(row) => `${row.key}:${row.period_start}`} rowClassName={(row) => (row.gap_hours < 0 ? "row-late" : row.utilization_pct >= 85 ? "row-at-risk" : undefined)} initialSort={{ key: "period", direction: "asc" }} filters dense pageSize={100} ariaLabel="Capacity per period" />}
        </AsyncContent>
      </Section>

      {report && (report.notes.length > 0 || report.unallocated_hours > 0) ? (
        <Section title="Notes from the capacity engine">
          <ul className="reason-list text-sm text-muted">
            {report.unallocated_hours > 0 ? (
              <li>
                {formatWorkHours(report.unallocated_hours, 1)} of demand ({formatNumber(report.unallocated_operations)} operations) could not be attributed to any {humanize(report.dimension).toLowerCase()} and are not in the table.
              </li>
            ) : null}
            {report.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}
