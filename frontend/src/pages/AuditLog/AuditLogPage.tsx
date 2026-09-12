import { useMemo, useState } from "react";

import { useAudit } from "@/api/audit";
import type { AuditEntry } from "@/api/types";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { FilterBar } from "@/components/FilterBar";
import { Modal } from "@/components/Modal";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { humanize } from "@/lib/constants";
import { formatDateTime } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["entity_type", "entity_id", "user_id", "action"] as const;

function pretty(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function short(value: unknown, max = 60): string {
  const text = pretty(value).replace(/\s+/g, " ");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Immutable trail of overrides, locks, expedites, approvals and configuration changes. */
export default function AuditLogPage() {
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const params = useMemo(() => ({ entity_type: values.entity_type || undefined, entity_id: values.entity_id || undefined, user_id: values.user_id || undefined, action: values.action || undefined, limit: 500 }), [values]);
  const audit = useAudit(params);
  const [open, setOpen] = useState<AuditEntry | null>(null);

  const columns = useMemo<Column<AuditEntry>[]>(
    () => [
      { key: "ts", header: "Timestamp", cell: (e) => <span className="num">{formatDateTime(e.timestamp, "dd MMM yyyy HH:mm:ss")}</span>, sortValue: (e) => e.timestamp },
      { key: "user", header: "User", cell: (e) => <span className="mono">{e.username ?? e.user_id}</span>, sortValue: (e) => e.username ?? e.user_id, filterValue: (e) => `${e.user_id} ${e.username ?? ""}` },
      { key: "action", header: "Action", cell: (e) => <span className="strong">{humanize(e.action)}</span>, sortValue: (e) => e.action, filterValue: (e) => e.action },
      { key: "entity", header: "Entity", cell: (e) => <span><span className="text-muted">{e.entity_type} </span><span className="mono">{e.entity_id}</span></span>, sortValue: (e) => `${e.entity_type}:${e.entity_id}`, filterValue: (e) => `${e.entity_type} ${e.entity_id}` },
      { key: "prev", header: "Previous", cell: (e) => <span className="mono text-muted text-xs">{short(e.previous_value)}</span> },
      { key: "new", header: "New", cell: (e) => <span className="mono text-xs">{short(e.new_value)}</span> },
      { key: "reason", header: "Reason", cell: (e) => <span className="truncate" style={{ maxWidth: 280, display: "inline-block" }} title={e.reason ?? undefined}>{e.reason ?? "—"}</span>, filterValue: (e) => e.reason ?? "" },
    ],
    [],
  );

  return (
    <div className="page">
      <PageHeader eyebrow="System" title="Audit Log" subtitle="Who changed what, when, and why. Every override, lock, expedite, approval and configuration change is recorded." />
      <FilterBar
        fields={[
          { key: "entity_type", label: "Entity type", kind: "select", options: ["order", "schedule", "schedule_version", "priority_profile", "scheduling_config", "customer_rule", "alert", "user"].map((v) => ({ value: v, label: humanize(v) })) },
          { key: "entity_id", label: "Entity id", kind: "text", placeholder: "R3D-10482" },
          { key: "user_id", label: "User", kind: "text" },
          { key: "action", label: "Action", kind: "text", placeholder: "expedite" },
        ]}
        values={values}
        onChange={set}
        onReset={reset}
      />
      <Section title="Entries" count={audit.data?.meta.total} flush>
        <AsyncContent query={audit} isEmpty={(p) => p.items.length === 0} emptyTitle="No audit entries" emptyMessage="Overrides and approvals will appear here.">
          {(page) => <DataTable rows={page.items} columns={columns} rowKey={(e) => e.audit_id} onRowClick={setOpen} initialSort={{ key: "ts", direction: "desc" }} filters dense pageSize={50} totalCount={page.meta.total} />}
        </AsyncContent>
      </Section>
      <Modal open={open !== null} title={open ? `${humanize(open.action)} · ${open.entity_type} ${open.entity_id}` : ""} onClose={() => setOpen(null)} wide>
        {open ? (
          <div className="col gap-3">
            <dl className="kv">
              <dt>When</dt>
              <dd className="num">{formatDateTime(open.timestamp, "dd MMM yyyy HH:mm:ss")}</dd>
              <dt>User</dt>
              <dd className="mono">{open.username ?? open.user_id}</dd>
              <dt>Reason</dt>
              <dd>{open.reason ?? "—"}</dd>
            </dl>
            <div className="grid grid-2">
              <div>
                <h3>Previous value</h3>
                <pre className="mono text-xs" style={{ whiteSpace: "pre-wrap", background: "var(--bg-input)", padding: 8, borderRadius: 4, maxHeight: 320, overflow: "auto" }}>{pretty(open.previous_value)}</pre>
              </div>
              <div>
                <h3>New value</h3>
                <pre className="mono text-xs" style={{ whiteSpace: "pre-wrap", background: "var(--bg-input)", padding: 8, borderRadius: 4, maxHeight: 320, overflow: "auto" }}>{pretty(open.new_value)}</pre>
              </div>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
