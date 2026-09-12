/** Alerts inbox (spec Phase 20): severity · time · type · order/machine · reason · recommended action; acknowledge with a note. */
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAcknowledgeAlert, useAlertSummary, useAlerts } from "@/api/alerts";
import { describeError } from "@/api/client";
import type { Alert, AlertSeverity, AlertType } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { TextAreaField } from "@/components/Field";
import { FilterBar } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { Pager } from "@/components/Pager";
import { SeverityBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { useToast } from "@/components/Toast";
import { ALERT_SEVERITIES, ALERT_SEVERITY_RANK, ALERT_TYPES, humanize } from "@/lib/constants";
import { formatDateTime, formatRelative } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["severity", "alert_type", "acknowledged", "order_id", "machine_id", "page"] as const;
const REFRESH_MS = 60_000;

function AlertRef({ a }: { a: Alert }) {
  if (a.order_id) {
    return (
      <Link className="mono" to={routes.orderDetail(encodeURIComponent(a.order_id))} onClick={(e) => e.stopPropagation()}>
        {a.order_id}
      </Link>
    );
  }
  if (a.machine_id) {
    return (
      <Link className="mono" to={routes.machineDetail(encodeURIComponent(a.machine_id))} onClick={(e) => e.stopPropagation()}>
        {a.machine_id}
      </Link>
    );
  }
  return <span className="mono text-muted">{a.entity_ref ?? "—"}</span>;
}

export default function AlertsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { hasMinRole } = useAuth();
  const canAck = hasMinRole("supervisor");
  const now = useMemo(() => new Date(), []);
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const [ack, setAck] = useState<Alert | null>(null);
  const [note, setNote] = useState("");
  const [detail, setDetail] = useState<Alert | null>(null);
  const acknowledge = useAcknowledgeAlert();
  const page = Math.max(1, Number(values.page) || 1);

  const params = useMemo(
    () => ({
      page,
      page_size: 50,
      severity: (values.severity || undefined) as AlertSeverity | undefined,
      alert_type: (values.alert_type || undefined) as AlertType | undefined,
      order_id: values.order_id || undefined,
      machine_id: values.machine_id || undefined,
      acknowledged: values.acknowledged === "all" ? undefined : values.acknowledged === "true",
    }),
    [page, values],
  );
  const alerts = useAlerts(params, REFRESH_MS);
  const summary = useAlertSummary(REFRESH_MS);
  const rows = useMemo(() => alerts.data?.items ?? [], [alerts.data]);

  const columns = useMemo<Column<Alert>[]>(
    () => [
      { key: "sev", header: "Severity", width: 90, cell: (a) => <SeverityBadge severity={a.severity} />, sortValue: (a) => ALERT_SEVERITY_RANK[a.severity] },
      { key: "raised", header: "Time", cell: (a) => <span className="num text-nowrap" title={`raised ${formatDateTime(a.raised_at)} · last seen ${formatDateTime(a.last_seen_at)}`}>{formatRelative(a.raised_at, now)}{a.occurrences > 1 ? <span className="text-faint"> ×{a.occurrences}</span> : null}</span>, sortValue: (a) => a.raised_at },
      { key: "type", header: "Type", cell: (a) => humanize(a.alert_type), sortValue: (a) => a.alert_type, filterValue: (a) => a.alert_type },
      { key: "ref", header: "Order / machine", cell: (a) => <AlertRef a={a} />, filterValue: (a) => `${a.order_id ?? ""} ${a.machine_id ?? ""} ${a.entity_ref ?? ""}` },
      { key: "title", header: "Alert", cell: (a) => <span className="strong">{a.title}</span>, filterValue: (a) => `${a.title} ${a.reason}` },
      { key: "reason", header: "Reason", cell: (a) => <span className="text-muted truncate" style={{ maxWidth: 300, display: "inline-block" }} title={a.reason}>{a.reason}</span> },
      { key: "action", header: "Recommended action", cell: (a) => <span className="truncate" style={{ maxWidth: 300, display: "inline-block" }} title={a.recommended_action}>{a.recommended_action}</span> },
      {
        key: "ack",
        header: "Ack",
        cell: (a) =>
          a.acknowledged ? (
            <span className="text-muted text-xs" title={a.acknowledged_at ? formatDateTime(a.acknowledged_at) : undefined}>
              ✓ {a.acknowledged_by ?? "acknowledged"}
            </span>
          ) : canAck ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={(e) => {
                e.stopPropagation();
                setNote("");
                setAck(a);
              }}
            >
              Acknowledge
            </button>
          ) : (
            <span className="text-faint">open</span>
          ),
      },
    ],
    [now, canAck],
  );

  const confirmAck = async () => {
    if (!ack) return;
    try {
      await acknowledge.mutateAsync({ alertId: ack.alert_id, note: note.trim() || undefined });
      toast.push({ tone: "success", title: "Alert acknowledged", message: ack.title });
      setAck(null);
    } catch (err) {
      toast.push({ tone: "error", title: "Acknowledge failed", message: describeError(err) });
    }
  };

  const bySev = summary.data?.by_severity ?? {};

  return (
    <div className="page" data-testid="alerts-page">
      <PageHeader
        eyebrow="System"
        title="Alerts"
        subtitle="Exceptions raised by the engines: late risk, overdue, downtime, shortages, overload, bottlenecks, starvation, data quality. Refreshes every 60 s."
        actions={
          <button type="button" className="btn btn-sm" onClick={() => void Promise.all([alerts.refetch(), summary.refetch()])} disabled={alerts.isFetching}>
            {alerts.isFetching ? "Refreshing…" : "Refresh"}
          </button>
        }
      />
      <div className="grid grid-kpi" data-testid="alert-summary">
        <KpiCard label="Critical" value={bySev.critical ?? 0} tone="late" loading={summary.isPending} onClick={() => set("severity", "critical")} />
        <KpiCard label="High" value={bySev.high ?? 0} tone="blocked" loading={summary.isPending} onClick={() => set("severity", "high")} />
        <KpiCard label="Warning" value={bySev.warning ?? 0} tone="at-risk" loading={summary.isPending} onClick={() => set("severity", "warning")} />
        <KpiCard label="Info" value={bySev.info ?? 0} tone="running" loading={summary.isPending} onClick={() => set("severity", "info")} />
        <KpiCard label="Unacknowledged" value={summary.data?.unacknowledged ?? 0} tone={(summary.data?.unacknowledged ?? 0) > 0 ? "late" : "ready"} loading={summary.isPending} hint={`${summary.data?.total_active ?? 0} active`} />
      </div>
      <FilterBar
        fields={[
          { key: "severity", label: "Severity", kind: "select", options: ALERT_SEVERITIES.map((v) => ({ value: v, label: v })) },
          { key: "alert_type", label: "Type", kind: "select", options: ALERT_TYPES.map((v) => ({ value: v, label: humanize(v) })) },
          {
            key: "acknowledged",
            label: "State",
            kind: "select",
            options: [
              { value: "true", label: "Acknowledged" },
              { value: "all", label: "Open + acknowledged" },
            ],
          },
          { key: "order_id", label: "Order", kind: "text", placeholder: "SO…", width: 150 },
          { key: "machine_id", label: "Machine", kind: "text", placeholder: "MC-…", width: 150 },
        ]}
        values={values}
        onChange={(k, v) => {
          set(k, v);
          set("page", "");
        }}
        onReset={reset}
      />
      <Section title="Alerts" count={alerts.data?.meta.total} flush>
        {alerts.isPending ? (
          <LoadingState label="Loading alerts" />
        ) : alerts.isError ? (
          <ErrorState error={alerts.error} onRetry={() => void alerts.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState title="No alerts" message={values.acknowledged === "" ? "Nothing open needs attention with the current filters." : "No alerts match the current filters."} />
        ) : (
          <>
            <DataTable rows={rows} columns={columns} rowKey={(a) => a.alert_id} onRowClick={setDetail} rowClassName={(a) => (a.severity === "critical" ? "row-late" : a.severity === "high" ? "row-blocked" : a.severity === "warning" ? "row-at-risk" : undefined)} initialSort={{ key: "sev", direction: "desc" }} filters dense paginate={false} hideFooter ariaLabel="Alerts" />
            <Pager meta={alerts.data.meta} page={page} shown={rows.length} busy={alerts.isFetching} onPageChange={(p) => set("page", p <= 1 ? "" : String(p))} />
          </>
        )}
      </Section>

      <Modal
        open={ack !== null}
        title={ack ? `Acknowledge: ${ack.title}` : ""}
        onClose={() => setAck(null)}
        footer={
          <>
            <button type="button" className="btn" onClick={() => setAck(null)} disabled={acknowledge.isPending}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void confirmAck()} disabled={acknowledge.isPending} data-testid="ack-submit">
              {acknowledge.isPending ? "Acknowledging…" : "Acknowledge"}
            </button>
          </>
        }
      >
        {ack ? (
          <div className="col gap-3">
            <dl className="kv">
              <dt>Severity</dt>
              <dd>
                <SeverityBadge severity={ack.severity} />
              </dd>
              <dt>Reason</dt>
              <dd>{ack.reason}</dd>
              <dt>Recommended</dt>
              <dd>{ack.recommended_action}</dd>
            </dl>
            <TextAreaField label="Note (optional)" value={note} onChange={setNote} placeholder="What was done, or why no action is needed" help="Stored with the acknowledgement in the audit log." />
            {acknowledge.isError ? <div className="tone-late text-sm">{describeError(acknowledge.error)}</div> : null}
          </div>
        ) : null}
      </Modal>

      <Modal open={detail !== null} title={detail?.title ?? ""} onClose={() => setDetail(null)} wide>
        {detail ? (
          <div className="col gap-3">
            <div className="row row-wrap">
              <SeverityBadge severity={detail.severity} />
              <span>{humanize(detail.alert_type)}</span>
              <AlertRef a={detail} />
              <span className="text-faint num">raised {formatDateTime(detail.raised_at)} · seen {detail.occurrences}×</span>
              {detail.acknowledged ? <span className="pill pill-done pill-sm">ACKNOWLEDGED {detail.acknowledged_by ? `by ${detail.acknowledged_by}` : ""}</span> : null}
            </div>
            <dl className="kv">
              <dt>Reason</dt>
              <dd>{detail.reason}</dd>
              <dt>Recommended action</dt>
              <dd>{detail.recommended_action}</dd>
              {Object.entries(detail.details).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt className="mono">{k}</dt>
                  <dd className="mono text-muted">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
                </div>
              ))}
            </dl>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              {detail.order_id ? (
                <button type="button" className="btn" onClick={() => navigate(routes.orderDetail(encodeURIComponent(detail.order_id ?? "")))}>
                  Open order
                </button>
              ) : null}
              {detail.machine_id ? (
                <button type="button" className="btn" onClick={() => navigate(routes.machineDetail(encodeURIComponent(detail.machine_id ?? "")))}>
                  Open machine
                </button>
              ) : null}
              {canAck && !detail.acknowledged ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    setNote("");
                    setAck(detail);
                    setDetail(null);
                  }}
                >
                  Acknowledge
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
