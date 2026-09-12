/**
 * Audit Log (spec Phase 22): who changed what, when and why. Filters by entity, user, action and
 * date range; each row expands into a previous → new diff so "Why was this order scheduled at
 * 2:30 PM yesterday?" can be traced back to overrides, locks, expedites and configuration changes.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useAudit } from "@/api/audit";
import type { AuditEntry } from "@/api/types";
import { routes } from "@/app/nav";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar } from "@/components/FilterBar";
import { JsonDiff } from "@/components/JsonDiff";
import { LoadingState } from "@/components/LoadingState";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { Pager } from "@/components/Pager";
import { Section } from "@/components/Section";
import { AUDIT_ENTITY_TYPES, humanize } from "@/lib/constants";
import { diffJson, formatJsonValue } from "@/lib/diff";
import { formatDateTime, toUtcIso } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["entity_type", "entity_id", "user", "action", "from", "to", "page"] as const;

function dayBoundary(date: string, end: boolean): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T${end ? "23:59:59" : "00:00:00"}`);
  return Number.isNaN(d.getTime()) ? undefined : toUtcIso(d);
}

function entityLink(e: AuditEntry) {
  if (e.entity_type === "order") return routes.orderDetail(encodeURIComponent(e.entity_id));
  if (e.entity_type === "machine") return routes.machineDetail(encodeURIComponent(e.entity_id));
  return null;
}

/** Compact inline summary of the change for the table row. */
function ChangeSummary({ e }: { e: AuditEntry }) {
  const rows = useMemo(() => diffJson(e.previous_value, e.new_value), [e.previous_value, e.new_value]);
  if (e.previous_value === null && e.new_value === null) return <span className="text-faint">—</span>;
  if (rows.length === 0) return <span className="text-faint">no field changes</span>;
  const shown = rows.slice(0, 2);
  return (
    <span className="text-xs mono" title={rows.map((r) => `${r.path}: ${formatJsonValue(r.before)} → ${formatJsonValue(r.after)}`).join("\n")}>
      {shown.map((r) => (
        <span key={r.path} style={{ marginRight: 8 }}>
          <span className="text-muted">{r.path}</span> <span className="tone-late">{formatJsonValue(r.before, 24)}</span> → <span className="tone-ready">{formatJsonValue(r.after, 24)}</span>
        </span>
      ))}
      {rows.length > shown.length ? <span className="text-faint">+{rows.length - shown.length} more</span> : null}
    </span>
  );
}

export default function AuditLogPage() {
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const page = Math.max(1, Number(values.page) || 1);
  const params = useMemo(
    () => ({
      page,
      page_size: 50,
      entity_type: values.entity_type || undefined,
      entity_id: values.entity_id || undefined,
      user: values.user || undefined,
      action: values.action || undefined,
      from: dayBoundary(values.from, false),
      to: dayBoundary(values.to, true),
    }),
    [page, values],
  );
  const audit = useAudit(params);
  const rows = useMemo(() => audit.data?.items ?? [], [audit.data]);
  const [open, setOpen] = useState<AuditEntry | null>(null);

  const columns = useMemo<Column<AuditEntry>[]>(
    () => [
      { key: "ts", header: "Timestamp", cell: (e) => <span className="num text-nowrap">{formatDateTime(e.timestamp, "dd MMM yyyy HH:mm:ss")}</span>, sortValue: (e) => e.timestamp },
      { key: "user", header: "User", cell: (e) => <span className="mono">{e.user_id}</span>, sortValue: (e) => e.user_id, filterValue: (e) => e.user_id },
      { key: "action", header: "Action", cell: (e) => <span className="strong">{humanize(e.action)}</span>, sortValue: (e) => e.action, filterValue: (e) => e.action },
      {
        key: "entity",
        header: "Entity",
        cell: (e) => {
          const link = entityLink(e);
          return (
            <span>
              <span className="text-muted">{e.entity_type} </span>
              {link ? (
                <Link className="mono" to={link} onClick={(ev) => ev.stopPropagation()}>
                  {e.entity_id}
                </Link>
              ) : (
                <span className="mono">{e.entity_id}</span>
              )}
            </span>
          );
        },
        sortValue: (e) => `${e.entity_type}:${e.entity_id}`,
        filterValue: (e) => `${e.entity_type} ${e.entity_id}`,
      },
      { key: "change", header: "Previous → new", cell: (e) => <ChangeSummary e={e} /> },
      { key: "reason", header: "Reason", cell: (e) => <span className="truncate" style={{ maxWidth: 300, display: "inline-block" }} title={e.reason ?? undefined}>{e.reason ?? <span className="text-faint">—</span>}</span>, filterValue: (e) => e.reason ?? "" },
    ],
    [],
  );

  return (
    <div className="page" data-testid="audit-log-page">
      <PageHeader eyebrow="System" title="Audit Log" subtitle="Who changed what, when, and why. Every override, lock, expedite, acknowledgement, user and configuration change is recorded with its previous and new value." />
      <FilterBar
        fields={[
          { key: "entity_type", label: "Entity type", kind: "select", options: AUDIT_ENTITY_TYPES.map((v) => ({ value: v, label: humanize(v) })) },
          { key: "entity_id", label: "Entity id", kind: "text", placeholder: "SO2609-00093-02", width: 190 },
          { key: "user", label: "User id", kind: "text", placeholder: "usr_…", width: 170 },
          { key: "action", label: "Action", kind: "text", placeholder: "expedite", width: 150 },
          { key: "from", label: "From", kind: "date", width: 150 },
          { key: "to", label: "To", kind: "date", width: 150 },
        ]}
        values={values}
        onChange={(k, v) => {
          set(k, v);
          set("page", "");
        }}
        onReset={reset}
      />
      <Section title="Entries" count={audit.data?.meta.total} flush>
        {audit.isPending ? (
          <LoadingState label="Loading audit trail" />
        ) : audit.isError ? (
          <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState title="No audit entries" message="Overrides, locks, expedites, approvals and configuration changes will appear here." />
        ) : (
          <>
            <DataTable rows={rows} columns={columns} rowKey={(e) => e.audit_id} onRowClick={setOpen} initialSort={{ key: "ts", direction: "desc" }} filters dense paginate={false} hideFooter ariaLabel="Audit log" />
            <Pager meta={audit.data.meta} page={page} shown={rows.length} busy={audit.isFetching} onPageChange={(p) => set("page", p <= 1 ? "" : String(p))} />
          </>
        )}
      </Section>
      <Modal open={open !== null} title={open ? `${humanize(open.action)} · ${open.entity_type} ${open.entity_id}` : ""} onClose={() => setOpen(null)} wide>
        {open ? (
          <div className="col gap-3" data-testid="audit-entry-detail">
            <dl className="kv">
              <dt>When</dt>
              <dd className="num">{formatDateTime(open.timestamp, "dd MMM yyyy HH:mm:ss")}</dd>
              <dt>User</dt>
              <dd className="mono">{open.user_id}</dd>
              <dt>Reason</dt>
              <dd>{open.reason ?? "—"}</dd>
              {open.request_id ? (
                <>
                  <dt>Request id</dt>
                  <dd className="mono text-faint">{open.request_id}</dd>
                </>
              ) : null}
              {Object.entries(open.details).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt className="mono">{k}</dt>
                  <dd className="mono text-muted">{formatJsonValue(v, 200)}</dd>
                </div>
              ))}
            </dl>
            <JsonDiff before={open.previous_value} after={open.new_value} />
            <details>
              <summary className="text-xs text-muted">Raw values</summary>
              <div className="grid grid-2 mt-2">
                <pre className="mono text-xs" style={{ whiteSpace: "pre-wrap", background: "var(--bg-input)", padding: 8, borderRadius: 4, maxHeight: 280, overflow: "auto" }}>{JSON.stringify(open.previous_value, null, 2) ?? "null"}</pre>
                <pre className="mono text-xs" style={{ whiteSpace: "pre-wrap", background: "var(--bg-input)", padding: 8, borderRadius: 4, maxHeight: 280, overflow: "auto" }}>{JSON.stringify(open.new_value, null, 2) ?? "null"}</pre>
              </div>
            </details>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
