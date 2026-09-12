import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAcknowledgeAlert, useAlerts } from "@/api/alerts";
import { describeError } from "@/api/client";
import type { Alert, AlertSeverity, AlertType } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column } from "@/components/DataTable";
import { FilterBar } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { SeverityBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { useToast } from "@/components/Toast";
import { humanize } from "@/lib/constants";
import { formatDateTime, formatRelative } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["severity", "alert_type", "acknowledged"] as const;
const ALERT_TYPES: AlertType[] = ["order_likely_late", "order_overdue", "machine_downtime", "material_shortage", "tool_shortage", "capacity_overload", "bottleneck", "sla_breach_risk", "production_behind_schedule", "schedule_disruption", "starvation", "data_quality"];

/** Alert inbox: filter, drill into the order/machine, acknowledge with a note. */
export default function AlertsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { hasMinRole } = useAuth();
  const now = useMemo(() => new Date(), []);
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const [ack, setAck] = useState<Alert | null>(null);
  const acknowledge = useAcknowledgeAlert();

  const params = useMemo(
    () => ({
      severity: (values.severity || undefined) as AlertSeverity | undefined,
      alert_type: (values.alert_type || undefined) as AlertType | undefined,
      acknowledged: values.acknowledged === "" ? false : values.acknowledged === "true",
      limit: 500,
    }),
    [values],
  );
  const alerts = useAlerts(params);
  const items = useMemo(() => alerts.data?.items ?? [], [alerts.data]);
  const counts = useMemo(() => ({ critical: items.filter((a) => a.severity === "critical").length, high: items.filter((a) => a.severity === "high").length, warning: items.filter((a) => a.severity === "warning").length, info: items.filter((a) => a.severity === "info").length }), [items]);

  const columns = useMemo<Column<Alert>[]>(
    () => [
      { key: "sev", header: "Severity", width: 90, cell: (a) => <SeverityBadge severity={a.severity} />, sortValue: (a) => ({ critical: 4, high: 3, warning: 2, info: 1 })[a.severity] },
      { key: "type", header: "Type", cell: (a) => humanize(a.alert_type), sortValue: (a) => a.alert_type, filterValue: (a) => a.alert_type },
      { key: "title", header: "Alert", cell: (a) => <span className="strong">{a.title}</span>, filterValue: (a) => `${a.title} ${a.reason}` },
      { key: "reason", header: "Reason", cell: (a) => <span className="text-muted truncate" style={{ maxWidth: 320, display: "inline-block" }} title={a.reason}>{a.reason}</span> },
      { key: "action", header: "Recommended action", cell: (a) => <span className="truncate" style={{ maxWidth: 280, display: "inline-block" }} title={a.recommended_action}>{a.recommended_action}</span> },
      { key: "ref", header: "Ref", cell: (a) => <span className="mono">{a.order_id ?? a.machine_id ?? a.entity_ref ?? "—"}</span>, filterValue: (a) => `${a.order_id ?? ""} ${a.machine_id ?? ""} ${a.entity_ref ?? ""}` },
      { key: "raised", header: "Raised", cell: (a) => <span className="num" title={formatDateTime(a.raised_at)}>{formatRelative(a.raised_at, now)}</span>, sortValue: (a) => a.raised_at },
      { key: "ack", header: "Ack", cell: (a) => (a.acknowledged ? <span className="text-muted text-xs">{a.acknowledged_by ?? "yes"}</span> : hasMinRole("supervisor") ? <button type="button" className="btn btn-sm" onClick={(e) => { e.stopPropagation(); setAck(a); }}>Acknowledge</button> : "—") },
    ],
    [now, hasMinRole],
  );

  const confirmAck = async (note: string) => {
    if (!ack) return;
    try {
      await acknowledge.mutateAsync({ alertId: ack.alert_id, note: note || undefined });
      toast.push({ tone: "success", title: "Alert acknowledged" });
      setAck(null);
    } catch (err) {
      toast.push({ tone: "error", title: "Acknowledge failed", message: describeError(err) });
    }
  };

  return (
    <div className="page">
      <PageHeader eyebrow="System" title="Alerts" subtitle="Exceptions raised by the engines: late risk, downtime, shortages, overload, starvation, data quality." />
      <div className="grid grid-kpi">
        <KpiCard label="Critical" value={counts.critical} tone="late" loading={alerts.isPending} />
        <KpiCard label="High" value={counts.high} tone="blocked" loading={alerts.isPending} />
        <KpiCard label="Warning" value={counts.warning} tone="at-risk" loading={alerts.isPending} />
        <KpiCard label="Info" value={counts.info} tone="running" loading={alerts.isPending} />
        <KpiCard label="Total shown" value={alerts.data?.meta.total ?? items.length} tone="neutral" loading={alerts.isPending} />
      </div>
      <FilterBar
        fields={[
          { key: "severity", label: "Severity", kind: "select", options: ["critical", "high", "warning", "info"].map((v) => ({ value: v, label: v })) },
          { key: "alert_type", label: "Type", kind: "select", options: ALERT_TYPES.map((v) => ({ value: v, label: humanize(v) })) },
          { key: "acknowledged", label: "State", kind: "select", options: [{ value: "false", label: "Open" }, { value: "true", label: "Acknowledged" }] },
        ]}
        values={values}
        onChange={set}
        onReset={reset}
      />
      <Section title="Alerts" count={items.length} flush>
        <AsyncContent query={alerts} isEmpty={(p) => p.items.length === 0} emptyTitle="No alerts" emptyMessage="Nothing needs attention with the current filters.">
          {(page) => (
            <DataTable
              rows={page.items}
              columns={columns}
              rowKey={(a) => a.alert_id}
              onRowClick={(a) => (a.order_id ? navigate(routes.orderDetail(encodeURIComponent(a.order_id))) : a.machine_id ? navigate(routes.machineDetail(encodeURIComponent(a.machine_id))) : undefined)}
              rowClassName={(a) => (a.severity === "critical" ? "row-late" : a.severity === "high" ? "row-blocked" : a.severity === "warning" ? "row-at-risk" : undefined)}
              initialSort={{ key: "sev", direction: "desc" }}
              filters
              dense
              pageSize={50}
              totalCount={page.meta.total}
            />
          )}
        </AsyncContent>
      </Section>
      <ConfirmDialog open={ack !== null} title={ack ? `Acknowledge: ${ack.title}` : ""} message={ack?.recommended_action} confirmLabel="Acknowledge" requireReason={false} busy={acknowledge.isPending} onConfirm={(note) => void confirmAck(note)} onCancel={() => setAck(null)} />
    </div>
  );
}
