import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { describeError } from "@/api/client";
import { useDataQuality, useRunDataQuality } from "@/api/dataQuality";
import type { DataQualityCode, DataQualityIssue, DataQualitySeverity } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { FilterBar } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { DataQualitySeverityBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { useToast } from "@/components/Toast";
import { humanize } from "@/lib/constants";
import { formatDateTime } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["severity", "code", "entity_type"] as const;
const CODES: DataQualityCode[] = ["missing_due_date", "invalid_date", "missing_cycle_time", "missing_setup_time", "missing_machine_assignment", "missing_material", "negative_quantity", "duplicate_order", "incorrect_status", "impossible_production_time", "missing_customer", "conflicting_machine_capability", "invalid_routing", "missing_operations", "unknown_reference"];

const columns: Column<DataQualityIssue>[] = [
  { key: "sev", header: "Severity", width: 90, cell: (i) => <DataQualitySeverityBadge severity={i.severity} />, sortValue: (i) => ({ blocking: 3, warning: 2, info: 1 })[i.severity] },
  { key: "code", header: "Code", cell: (i) => <span className="mono">{i.code}</span>, sortValue: (i) => i.code, filterValue: (i) => i.code },
  { key: "entity", header: "Entity", cell: (i) => <span><span className="text-muted">{i.entity_type} </span><span className="mono strong">{i.entity_id}</span></span>, sortValue: (i) => `${i.entity_type}:${i.entity_id}`, filterValue: (i) => `${i.entity_type} ${i.entity_id}` },
  { key: "field", header: "Field", cell: (i) => <span className="mono text-muted">{i.field_name ?? "—"}</span>, filterValue: (i) => i.field_name ?? "" },
  { key: "message", header: "Message", cell: (i) => i.message, filterValue: (i) => i.message },
  { key: "rec", header: "Recommendation", cell: (i) => <span className="text-muted">{i.recommendation ?? "—"}</span> },
  { key: "at", header: "Detected", cell: (i) => <span className="num">{formatDateTime(i.detected_at)}</span>, sortValue: (i) => i.detected_at ?? "" },
];

/** ERP data issues found by the data-quality engine: blocking items stop scheduling for that entity. */
export default function DataQualityPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { hasMinRole } = useAuth();
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const params = useMemo(() => ({ severity: (values.severity || undefined) as DataQualitySeverity | undefined, code: (values.code || undefined) as DataQualityCode | undefined, entity_type: values.entity_type || undefined, limit: 1000 }), [values]);
  const issues = useDataQuality(params);
  const run = useRunDataQuality();
  const items = useMemo(() => issues.data?.items ?? [], [issues.data]);
  const counts = useMemo(() => ({ blocking: items.filter((i) => i.severity === "blocking").length, warning: items.filter((i) => i.severity === "warning").length, info: items.filter((i) => i.severity === "info").length, entities: new Set(items.map((i) => `${i.entity_type}:${i.entity_id}`)).size }), [items]);
  const byCode = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of items) m.set(i.code, (m.get(i.code) ?? 0) + 1);
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [items]);

  const onRun = async () => {
    try {
      const r = await run.mutateAsync();
      toast.push({ tone: r.blocking > 0 ? "warning" : "success", title: "Data quality run complete", message: `${r.blocking} blocking · ${r.warning} warning · ${r.info} info` });
    } catch (err) {
      toast.push({ tone: "error", title: "Run failed", message: describeError(err) });
    }
  };

  return (
    <div className="page">
      <PageHeader eyebrow="System" title="Data Quality" subtitle="Missing or inconsistent ERP data. Blocking issues exclude the entity from scheduling until fixed in the ERP." actions={hasMinRole("planner") ? <button type="button" className="btn btn-primary" onClick={() => void onRun()} disabled={run.isPending}>{run.isPending ? "Running…" : "Run checks now"}</button> : null} />
      <div className="grid grid-kpi">
        <KpiCard label="Blocking" value={counts.blocking} tone="late" loading={issues.isPending} />
        <KpiCard label="Warning" value={counts.warning} tone="at-risk" loading={issues.isPending} />
        <KpiCard label="Info" value={counts.info} tone="neutral" loading={issues.isPending} />
        <KpiCard label="Entities affected" value={counts.entities} tone="blocked" loading={issues.isPending} />
        <KpiCard label="Top issue" value={byCode[0] ? humanize(byCode[0][0]) : "—"} tone="neutral" hint={byCode[0] ? `${byCode[0][1]} occurrences` : undefined} loading={issues.isPending} />
      </div>
      <FilterBar
        fields={[
          { key: "severity", label: "Severity", kind: "select", options: ["blocking", "warning", "info"].map((v) => ({ value: v, label: v })) },
          { key: "code", label: "Code", kind: "select", options: CODES.map((v) => ({ value: v, label: humanize(v) })) },
          { key: "entity_type", label: "Entity", kind: "select", options: ["order", "operation", "machine", "customer", "snapshot"].map((v) => ({ value: v, label: v })) },
        ]}
        values={values}
        onChange={set}
        onReset={reset}
      />
      <Section title="Issues" count={items.length} flush>
        <AsyncContent query={issues} isEmpty={(p) => p.items.length === 0} emptyTitle="No data quality issues" emptyMessage="Run the checks to validate the latest sync.">
          {(page) => <DataTable rows={page.items} columns={columns} rowKey={(i) => i.issue_id ?? `${i.code}:${i.entity_type}:${i.entity_id}:${i.field_name ?? ""}`} onRowClick={(i) => (i.entity_type === "order" ? navigate(routes.orderDetail(encodeURIComponent(i.entity_id))) : i.entity_type === "machine" ? navigate(routes.machineDetail(encodeURIComponent(i.entity_id))) : undefined)} rowClassName={(i) => (i.severity === "blocking" ? "row-late" : i.severity === "warning" ? "row-at-risk" : undefined)} initialSort={{ key: "sev", direction: "desc" }} filters dense pageSize={100} totalCount={page.meta.total} />}
        </AsyncContent>
      </Section>
    </div>
  );
}
