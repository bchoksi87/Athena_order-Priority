/** Data Quality dashboard (spec Phase 21): "N orders cannot be scheduled because: …" plus the issue list. */
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { describeError } from "@/api/client";
import { useDataQualityIssues, useDataQualitySummary, useRunDataQuality } from "@/api/dataQuality";
import type { DataQualityCode, DataQualityIssue, DataQualitySeverity } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { BarList } from "@/components/BarList";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { Pager } from "@/components/Pager";
import { DataQualitySeverityBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { useToast } from "@/components/Toast";
import { DQ_CODES, DQ_SEVERITIES, DQ_SEVERITY_RANK, humanize } from "@/lib/constants";
import { formatNumber } from "@/lib/formatters";
import { formatDateTime, formatRelative } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["severity", "code", "entity_type", "entity_id", "page"] as const;

const columns: Column<DataQualityIssue>[] = [
  { key: "sev", header: "Severity", width: 90, cell: (i) => <DataQualitySeverityBadge severity={i.severity} />, sortValue: (i) => DQ_SEVERITY_RANK[i.severity] ?? 0 },
  { key: "code", header: "Code", cell: (i) => <span className="mono">{i.code}</span>, sortValue: (i) => i.code, filterValue: (i) => i.code },
  { key: "entity", header: "Entity", cell: (i) => <span><span className="text-muted">{i.entity_type} </span><span className="mono strong">{i.entity_id}</span></span>, sortValue: (i) => `${i.entity_type}:${i.entity_id}`, filterValue: (i) => `${i.entity_type} ${i.entity_id}` },
  { key: "field", header: "Field", cell: (i) => <span className="mono text-muted">{i.field_name ?? "—"}</span>, filterValue: (i) => i.field_name ?? "" },
  { key: "message", header: "Message", cell: (i) => i.message, filterValue: (i) => i.message },
  { key: "rec", header: "Recommendation", cell: (i) => <span className="text-muted">{i.recommendation ?? "—"}</span> },
  { key: "at", header: "Detected", cell: (i) => <span className="num text-nowrap">{formatDateTime(i.detected_at, "dd MMM HH:mm")}</span>, sortValue: (i) => i.detected_at },
];

export default function DataQualityPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { hasMinRole } = useAuth();
  const now = useMemo(() => new Date(), []);
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const page = Math.max(1, Number(values.page) || 1);
  const params = useMemo(
    () => ({
      page,
      page_size: 100,
      severity: (values.severity || undefined) as DataQualitySeverity | undefined,
      code: (values.code || undefined) as DataQualityCode | undefined,
      entity_type: values.entity_type || undefined,
      entity_id: values.entity_id || undefined,
    }),
    [page, values],
  );
  const summary = useDataQualitySummary();
  const issues = useDataQualityIssues(params);
  const run = useRunDataQuality();
  const rows = useMemo(() => issues.data?.items ?? [], [issues.data]);
  const s = summary.data;
  const dash = s?.dashboard;

  const onRun = async () => {
    try {
      const r = await run.mutateAsync();
      toast.push({ tone: (r.by_severity.blocking ?? 0) > 0 ? "warning" : "success", title: "Data quality run complete", message: `${r.total_issues} issues · ${r.by_severity.blocking ?? 0} blocking · ${r.by_severity.warning ?? 0} warning · ${r.by_severity.info ?? 0} info` });
    } catch (err) {
      toast.push({ tone: "error", title: "Run failed", message: describeError(err) });
    }
  };

  const entityTypes = useMemo(() => Array.from(new Set([...Object.keys(s?.by_entity_type ?? {}), "order", "operation", "machine", "customer", "material", "tooling"])), [s]);

  return (
    <div className="page" data-testid="data-quality-page">
      <PageHeader
        eyebrow="System"
        title="Data Quality"
        subtitle="Missing or inconsistent ERP data. Blocking issues exclude the entity from scheduling until fixed in the ERP; the engine never assumes the data is perfect."
        actions={
          <>
            {s?.detected_at ? <span className="text-xs text-faint">last run {formatRelative(s.detected_at, now)} · {s.run_id}</span> : <span className="text-xs text-faint">no run yet</span>}
            {hasMinRole("planner") ? (
              <button type="button" className="btn btn-primary" onClick={() => void onRun()} disabled={run.isPending}>
                {run.isPending ? "Running…" : "Run data quality check"}
              </button>
            ) : null}
          </>
        }
      />

      {summary.isPending ? (
        <LoadingState compact label="Loading dashboard" />
      ) : summary.isError ? (
        <ErrorState compact error={summary.error} onRetry={() => void summary.refetch()} />
      ) : dash ? (
        <>
          <div className="grid grid-kpi">
            <KpiCard label="Open orders" value={formatNumber(dash.open_orders)} tone="neutral" />
            <KpiCard label="Cannot be scheduled" value={formatNumber(dash.unschedulable_orders)} tone={dash.unschedulable_orders > 0 ? "late" : "ready"} hint={dash.open_orders > 0 ? `${((100 * dash.unschedulable_orders) / dash.open_orders).toFixed(0)}% of open orders` : undefined} />
            <KpiCard label="Blocking" value={formatNumber(s.by_severity.blocking ?? 0)} tone="late" onClick={() => set("severity", "blocking")} />
            <KpiCard label="Warning" value={formatNumber(s.by_severity.warning ?? 0)} tone="at-risk" onClick={() => set("severity", "warning")} />
            <KpiCard label="Info" value={formatNumber(s.by_severity.info ?? 0)} tone="neutral" onClick={() => set("severity", "info")} />
            <KpiCard label="Entities affected" value={formatNumber(s.blocked_entities)} tone="blocked" hint={Object.entries(s.by_entity_type).map(([k, v]) => `${v} ${k}`).join(" · ") || undefined} />
          </div>
          <div className="grid grid-2">
            <Section title="Headline">
              <div className="dq-headline" data-testid="dq-headline">
                {dash.headline}
              </div>
              <div className="text-xs text-faint mt-2">as of {formatDateTime(dash.as_of)} · primary reason per order</div>
              <div className="mt-4">
                <BarList
                  items={dash.reasons.map((r) => ({ key: r.code, label: r.label, value: r.orders, tone: "late" as const, onClick: () => set("code", r.code) }))}
                  total={dash.unschedulable_orders}
                  emptyMessage="All open orders pass the blocking checks."
                />
              </div>
            </Section>
            <Section title="Issues by code">
              <BarList
                items={Object.entries(s.by_code)
                  .sort((a, b) => b[1] - a[1])
                  .map(([code, n]) => ({ key: code, label: humanize(code), value: n, tone: (dash.orders_by_code[code] ?? 0) > 0 ? ("late" as const) : ("at-risk" as const), onClick: () => set("code", code) }))}
                total={s.total_issues}
                emptyMessage="No issues recorded by the latest run."
              />
              {Object.keys(dash.warnings_by_code).length > 0 ? <div className="text-xs text-faint mt-2">warnings: {Object.entries(dash.warnings_by_code).map(([k, v]) => `${humanize(k)} ${v}`).join(" · ")}</div> : null}
            </Section>
          </div>
        </>
      ) : null}

      <FilterBar
        fields={[
          { key: "severity", label: "Severity", kind: "select", options: DQ_SEVERITIES.map((v) => ({ value: v, label: v })) },
          { key: "code", label: "Code", kind: "select", options: DQ_CODES.map((v) => ({ value: v, label: humanize(v) })) },
          { key: "entity_type", label: "Entity type", kind: "select", options: entityTypes.map((v) => ({ value: v, label: v })) },
          { key: "entity_id", label: "Entity id", kind: "text", placeholder: "SO… / MC-…", width: 170 },
        ]}
        values={values}
        onChange={(k, v) => {
          set(k, v);
          set("page", "");
        }}
        onReset={reset}
      />
      <Section title="Issues" count={issues.data?.meta.total} flush>
        {issues.isPending ? (
          <LoadingState label="Loading issues" />
        ) : issues.isError ? (
          <ErrorState error={issues.error} onRetry={() => void issues.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState title="No data quality issues" message={s?.run_id ? "Nothing matches the current filters." : "Run the checks to validate the latest sync."} />
        ) : (
          <>
            <DataTable rows={rows} columns={columns} rowKey={(i) => i.issue_id} onRowClick={(i) => (i.entity_type === "order" ? navigate(routes.orderDetail(encodeURIComponent(i.entity_id))) : i.entity_type === "machine" ? navigate(routes.machineDetail(encodeURIComponent(i.entity_id))) : undefined)} rowClassName={(i) => (i.severity === "blocking" ? "row-late" : i.severity === "warning" ? "row-at-risk" : undefined)} initialSort={{ key: "sev", direction: "desc" }} filters dense paginate={false} hideFooter ariaLabel="Data quality issues" />
            <Pager meta={issues.data.meta} page={page} shown={rows.length} busy={issues.isFetching} onPageChange={(p) => set("page", p <= 1 ? "" : String(p))} />
          </>
        )}
      </Section>
    </div>
  );
}
