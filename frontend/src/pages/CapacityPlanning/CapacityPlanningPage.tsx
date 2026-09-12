import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useCapacity, type CapacityQuery } from "@/api/analytics";
import type { CapacityRow } from "@/api/types";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { FilterBar } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { axisTick, chartColors, tooltipStyle } from "@/lib/chartTheme";
import { formatHours, formatPct } from "@/lib/formatters";
import { formatDateTime } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["dimension", "bucket"] as const;

const columns: Column<CapacityRow>[] = [
  { key: "key", header: "Resource", cell: (r) => <span className="strong">{r.key}</span>, sortValue: (r) => r.key, filterValue: (r) => r.key },
  { key: "period", header: "Period", cell: (r) => <span className="num">{formatDateTime(r.period_start, "dd MMM")} – {formatDateTime(r.period_end, "dd MMM")}</span>, sortValue: (r) => r.period_start },
  { key: "req", header: "Required", numeric: true, cell: (r) => formatHours(r.required_hours), sortValue: (r) => r.required_hours },
  { key: "avail", header: "Available", numeric: true, cell: (r) => formatHours(r.available_hours), sortValue: (r) => r.available_hours },
  { key: "gap", header: "Gap", numeric: true, cell: (r) => <span className={(r.gap_hours ?? 0) < 0 ? "tone-late strong" : "tone-ready"}>{formatHours(r.gap_hours)}</span>, sortValue: (r) => r.gap_hours ?? 0 },
  { key: "util", header: "Load", numeric: true, cell: (r) => <span className={(r.utilization_pct ?? 0) >= 100 ? "tone-late strong" : (r.utilization_pct ?? 0) >= 85 ? "tone-at-risk" : ""}>{formatPct(r.utilization_pct, 0)}</span>, sortValue: (r) => r.utilization_pct ?? 0 },
];

/** Required vs available hours per machine group / process / machine over the horizon. */
export default function CapacityPlanningPage() {
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const params = useMemo<CapacityQuery>(
    () => ({ dimension: (values.dimension || "machine_group") as CapacityQuery["dimension"], bucket: (values.bucket || "week") as CapacityQuery["bucket"] }),
    [values],
  );
  const capacity = useCapacity(params);
  const rows = useMemo(() => capacity.data ?? [], [capacity.data]);

  const byResource = useMemo(() => {
    const acc = new Map<string, { name: string; required: number; available: number }>();
    for (const r of rows) {
      const cur = acc.get(r.key) ?? { name: r.key, required: 0, available: 0 };
      cur.required += r.required_hours;
      cur.available += r.available_hours;
      acc.set(r.key, cur);
    }
    return Array.from(acc.values()).sort((a, b) => b.required / Math.max(1, b.available) - a.required / Math.max(1, a.available));
  }, [rows]);

  const totals = useMemo(() => {
    const required = rows.reduce((s, r) => s + r.required_hours, 0);
    const available = rows.reduce((s, r) => s + r.available_hours, 0);
    return { required, available, gap: available - required, overloaded: byResource.filter((r) => r.required > r.available).length };
  }, [rows, byResource]);

  return (
    <div className="page">
      <PageHeader eyebrow="Analyse" title="Capacity Planning" subtitle="Planned load against available working hours. Negative gaps mean overtime, outsourcing or re-prioritisation." />
      <FilterBar
        fields={[
          { key: "dimension", label: "Dimension", kind: "select", options: ["machine_group", "machine", "process", "department"].map((v) => ({ value: v, label: v.replace("_", " ") })) },
          { key: "bucket", label: "Bucket", kind: "select", options: [{ value: "day", label: "Day" }, { value: "week", label: "Week" }] },
        ]}
        values={values}
        onChange={set}
        onReset={reset}
      />
      <div className="grid grid-kpi">
        <KpiCard label="Required hours" value={formatHours(totals.required, 0)} tone="neutral" loading={capacity.isPending} />
        <KpiCard label="Available hours" value={formatHours(totals.available, 0)} tone="neutral" loading={capacity.isPending} />
        <KpiCard label="Net gap" value={formatHours(totals.gap, 0)} tone={totals.gap < 0 ? "late" : "ready"} loading={capacity.isPending} />
        <KpiCard label="Overall load" value={formatPct(totals.available > 0 ? (100 * totals.required) / totals.available : null, 0)} tone={totals.required > totals.available ? "late" : "running"} loading={capacity.isPending} />
        <KpiCard label="Overloaded resources" value={totals.overloaded} tone={totals.overloaded > 0 ? "blocked" : "ready"} loading={capacity.isPending} />
      </div>
      <Section title={`Required vs available by ${params.dimension?.replace("_", " ")}`}>
        <AsyncContent query={capacity} emptyTitle="No capacity data" emptyMessage="Generate a schedule to compute planned load." compact>
          {() => (
            <div className="chart-box" style={{ height: 280 }}>
              <ResponsiveContainer>
                <BarChart data={byResource} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                  <CartesianGrid stroke={chartColors.grid} vertical={false} />
                  <XAxis dataKey="name" tick={axisTick} axisLine={false} tickLine={false} interval={0} angle={byResource.length > 8 ? -30 : 0} height={byResource.length > 8 ? 60 : 30} textAnchor={byResource.length > 8 ? "end" : "middle"} />
                  <YAxis tick={axisTick} axisLine={false} tickLine={false} unit="h" />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v) => `${Number(v ?? 0).toFixed(0)}h`} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="available" name="Available" fill={chartColors.neutral} isAnimationActive={false} />
                  <Bar dataKey="required" name="Required" fill={chartColors.primary} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </AsyncContent>
      </Section>
      <Section title="Capacity rows" count={rows.length} flush>
        <AsyncContent query={capacity} emptyTitle="No capacity rows" compact>
          {(data) => <DataTable rows={data} columns={columns} rowKey={(r) => `${r.dimension}:${r.key}:${r.period_start}`} rowClassName={(r) => ((r.gap_hours ?? 0) < 0 ? "row-late" : (r.utilization_pct ?? 0) >= 85 ? "row-at-risk" : undefined)} initialSort={{ key: "util", direction: "desc" }} filters dense pageSize={100} />}
        </AsyncContent>
      </Section>
    </div>
  );
}
