/**
 * ERP synchronisation (admin, contract §9 / spec Phase 2): run a sync, the run history with per-entity
 * counts and reconciliation, connector status, and the Required / Available / Missing field report.
 */
import { useMemo, useState } from "react";

import { describeError } from "@/api/client";
import { useRunSync, useSyncCapabilities, useSyncRun, useSyncRuns, useSyncStatus } from "@/api/sync";
import type { CapabilityReportResponse, FieldAssessmentResponse, SyncMode, SyncRunResponse } from "@/api/types";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { CheckField, SelectField } from "@/components/Field";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { Modal } from "@/components/Modal";
import { Pager } from "@/components/Pager";
import { Section } from "@/components/Section";
import { StatusPill } from "@/components/StatusPill";
import { Tabs } from "@/components/Tabs";
import { useToast } from "@/components/Toast";
import { fieldAssessmentTone, humanize, syncStatusTone } from "@/lib/constants";
import { formatNumber, formatPct } from "@/lib/formatters";
import { formatDateTime, formatRelative } from "@/lib/time";

type Tab = "runs" | "capabilities" | "connector";

function sumCounts(counts: Record<string, number>): number {
  return Object.values(counts).reduce((s, n) => s + n, 0);
}

function durationText(r: SyncRunResponse): string {
  if (r.duration_seconds === null) return r.finished_at ? "—" : "running";
  return r.duration_seconds >= 60 ? `${(r.duration_seconds / 60).toFixed(1)} min` : `${r.duration_seconds.toFixed(1)} s`;
}

function EntityCountsTable({ fetched, upserted }: { fetched: Record<string, number>; upserted: Record<string, number> }) {
  const entities = Array.from(new Set([...Object.keys(fetched), ...Object.keys(upserted)]));
  if (entities.length === 0) return <div className="text-muted text-sm">No records reported.</div>;
  return (
    <table className="fld-map">
      <thead>
        <tr>
          <th>Entity</th>
          <th className="num">Fetched</th>
          <th className="num">Upserted</th>
        </tr>
      </thead>
      <tbody>
        {entities.map((e) => (
          <tr key={e}>
            <td className="mono">{e}</td>
            <td className="num">{formatNumber(fetched[e] ?? 0)}</td>
            <td className={`num ${(upserted[e] ?? 0) < (fetched[e] ?? 0) ? "tone-at-risk" : ""}`}>{formatNumber(upserted[e] ?? 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RunDetail({ runId, onClose }: { runId: string | null; onClose: () => void }) {
  const run = useSyncRun(runId ?? undefined);
  const r = run.data;
  return (
    <Modal open={runId !== null} title={runId ? `Sync run ${runId}` : ""} onClose={onClose} wide>
      {run.isPending ? (
        <LoadingState compact label="Loading run" />
      ) : run.isError ? (
        <ErrorState compact error={run.error} />
      ) : r ? (
        <div className="col gap-3" data-testid="sync-run-detail">
          <div className="row row-wrap gap-3">
            <StatusPill tone={syncStatusTone(r.status)}>{r.status}</StatusPill>
            <span className="mono">{r.mode}</span>
            <span className="text-muted">connector {r.connector}</span>
            <span className="num text-muted">
              {formatDateTime(r.started_at, "dd MMM HH:mm:ss")} → {formatDateTime(r.finished_at, "HH:mm:ss")} · {durationText(r)}
            </span>
            {r.triggered_by ? <span className="text-faint">by {r.triggered_by}</span> : null}
          </div>
          {r.error_message ? (
            <div className="state state-error state-compact" role="alert">
              <div className="state-message">{r.error_message}</div>
            </div>
          ) : null}
          <div className="grid grid-2">
            <Section title="Records">
              <EntityCountsTable fetched={r.records_fetched} upserted={r.records_upserted} />
              {Object.keys(r.stored_totals).length > 0 ? <div className="text-xs text-faint mt-2">stored after run: {Object.entries(r.stored_totals).map(([k, v]) => `${k} ${formatNumber(v)}`).join(" · ")}</div> : null}
              {r.pruned_orders > 0 ? <div className="text-xs tone-at-risk mt-2">{formatNumber(r.pruned_orders)} orders missing from the ERP were marked cancelled</div> : null}
              {r.orders_closed_missing > 0 ? <div className="text-xs text-muted mt-2">{formatNumber(r.orders_closed_missing)} orders closed because the ERP no longer reports them open</div> : null}
            </Section>
            <Section title="Reconciliation" count={r.reconciliation ? r.reconciliation.status : undefined}>
              {r.reconciliation ? (
                <>
                  <div className={`text-sm mb-2 tone-${syncStatusTone(r.reconciliation.status)}`}>{r.reconciliation.summary || "connector and stored counts agree"}</div>
                  <table className="fld-map">
                    <thead>
                      <tr>
                        <th>Entity</th>
                        <th className="num">Connector</th>
                        <th className="num">Stored</th>
                        <th className="num">Δ</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {r.reconciliation.deltas.map((d) => (
                        <tr key={d.entity}>
                          <td className="mono">{d.entity}</td>
                          <td className="num">{formatNumber(d.connector_count)}</td>
                          <td className="num">{formatNumber(d.stored_count)}</td>
                          <td className={`num ${d.delta !== 0 ? "tone-at-risk" : ""}`}>
                            {d.delta > 0 ? "+" : ""}
                            {formatNumber(d.delta)} ({formatPct(d.delta_pct, 1)})
                          </td>
                          <td>
                            <StatusPill tone={syncStatusTone(d.status)} size="sm">
                              {d.status}
                            </StatusPill>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              ) : (
                <div className="text-muted text-sm">No reconciliation for this run.</div>
              )}
            </Section>
          </div>
          <Section title="Validation issues" count={`${r.issues_count}${r.issues_truncated ? " (list truncated)" : ""}`}>
            {Object.keys(r.issue_counts).length > 0 ? <div className="text-sm mb-2">{Object.entries(r.issue_counts).map(([k, v]) => `${humanize(k)} ${formatNumber(v)}`).join(" · ")}</div> : <div className="text-muted text-sm">No validation issues.</div>}
            {r.issues.length > 0 ? (
              <table className="fld-map">
                <thead>
                  <tr>
                    <th>Stage</th>
                    <th>Entity</th>
                    <th>External id</th>
                    <th>Field</th>
                    <th>Code</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {r.issues.map((i, k) => (
                    <tr key={k}>
                      <td>{i.stage}</td>
                      <td className="mono">{i.entity}</td>
                      <td className="mono">{i.external_id}</td>
                      <td className="mono text-muted">{i.field ?? "—"}</td>
                      <td className="mono">{i.code}</td>
                      <td>{i.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </Section>
          <dl className="kv text-xs">
            <dt>Incremental since</dt>
            <dd className="num">{r.since ? formatDateTime(r.since, "dd MMM yyyy HH:mm:ss") : "— (full)"}</dd>
            <dt>Watermark source</dt>
            <dd className="mono">{r.watermark_source}</dd>
          </dl>
        </div>
      ) : null}
    </Modal>
  );
}

const runColumns: Column<SyncRunResponse>[] = [
  { key: "started", header: "Started", cell: (r) => <span className="num text-nowrap">{formatDateTime(r.started_at, "dd MMM HH:mm:ss")}</span>, sortValue: (r) => r.started_at },
  { key: "mode", header: "Mode", width: 90, cell: (r) => <span className="mono">{r.mode}</span>, sortValue: (r) => r.mode },
  { key: "status", header: "Status", width: 100, cell: (r) => <StatusPill tone={syncStatusTone(r.status)} size="sm">{r.status}</StatusPill>, sortValue: (r) => r.status },
  { key: "dur", header: "Duration", numeric: true, cell: (r) => durationText(r), sortValue: (r) => r.duration_seconds ?? -1 },
  { key: "fetched", header: "Fetched", numeric: true, cell: (r) => formatNumber(sumCounts(r.records_fetched)), sortValue: (r) => sumCounts(r.records_fetched), title: "Records fetched from the connector (all entities)" },
  { key: "upserted", header: "Upserted", numeric: true, cell: (r) => formatNumber(sumCounts(r.records_upserted)), sortValue: (r) => sumCounts(r.records_upserted) },
  { key: "issues", header: "Issues", numeric: true, cell: (r) => (r.issues_count > 0 ? <span className="tone-at-risk">{formatNumber(r.issues_count)}</span> : "0"), sortValue: (r) => r.issues_count },
  { key: "recon", header: "Reconciliation", cell: (r) => (r.reconciliation ? <span className={`tone-${syncStatusTone(r.reconciliation.status)}`} title={r.reconciliation.summary}>{r.reconciliation.status}</span> : <span className="text-faint">—</span>) },
  { key: "by", header: "Triggered by", cell: (r) => <span className="mono text-muted">{r.triggered_by ?? "scheduler"}</span> },
  { key: "err", header: "Error", cell: (r) => (r.error_message ? <span className="tone-late truncate" style={{ maxWidth: 260, display: "inline-block" }} title={r.error_message}>{r.error_message}</span> : "") },
];

function CapabilitiesTab({ report }: { report: CapabilityReportResponse }) {
  const [status, setStatus] = useState("");
  const [importance, setImportance] = useState("");
  const [entity, setEntity] = useState("");
  const statuses = useMemo(() => Array.from(new Set(report.assessments.map((a) => a.status))).sort(), [report]);
  const importances = useMemo(() => Array.from(new Set(report.assessments.map((a) => a.importance))), [report]);
  const entities = useMemo(() => Array.from(new Set(report.assessments.map((a) => a.entity))), [report]);
  const rows = useMemo(() => report.assessments.filter((a) => (!status || a.status === status) && (!importance || a.importance === importance) && (!entity || a.entity === entity)), [report, status, importance, entity]);
  const missingRequired = report.assessments.filter((a) => a.importance === "required" && a.status.toLowerCase() !== "available");
  const columns = useMemo<Column<FieldAssessmentResponse>[]>(
    () => [
      { key: "entity", header: "Entity", cell: (a) => <span className="mono">{a.entity}</span>, sortValue: (a) => a.entity, filterValue: (a) => a.entity },
      { key: "field", header: "Field", cell: (a) => <span className="mono strong">{a.field}</span>, sortValue: (a) => a.field, filterValue: (a) => a.field },
      { key: "importance", header: "Importance", width: 110, cell: (a) => <StatusPill tone={a.importance === "required" ? "late" : a.importance === "recommended" ? "at-risk" : "neutral"} size="sm" dot={false}>{a.importance}</StatusPill>, sortValue: (a) => (a.importance === "required" ? 0 : a.importance === "recommended" ? 1 : 2) },
      { key: "status", header: "Status", width: 100, cell: (a) => <StatusPill tone={fieldAssessmentTone(a.status, a.importance)} size="sm">{a.status}</StatusPill>, sortValue: (a) => a.status },
      { key: "used", header: "Used by", cell: (a) => <span className="text-muted">{a.used_by.join(", ") || "—"}</span>, filterValue: (a) => a.used_by.join(" ") },
      { key: "impact", header: "Impact if missing", cell: (a) => <span className="truncate" style={{ maxWidth: 320, display: "inline-block" }} title={a.impact_if_missing}>{a.impact_if_missing}</span> },
      { key: "rec", header: "Recommendation", cell: (a) => <span className="text-muted truncate" style={{ maxWidth: 320, display: "inline-block" }} title={a.recommendation}>{a.recommendation}</span> },
    ],
    [],
  );
  return (
    <div className="col gap-3" data-testid="sync-capabilities">
      <div className="grid grid-kpi">
        <KpiCard label="Field coverage" value={formatPct(report.coverage_pct, 0)} tone={report.coverage_pct >= 100 ? "ready" : report.coverage_pct >= 80 ? "at-risk" : "late"} hint={`${report.assessments.length} fields assessed · connector ${report.connector_name}`} />
        <KpiCard label="Can schedule" value={report.can_schedule ? "yes" : "no"} tone={report.can_schedule ? "ready" : "late"} hint={report.can_schedule ? "all required fields available" : `${report.missing_required.length} required field(s) missing`} />
        <KpiCard label="Missing required" value={missingRequired.length} tone={missingRequired.length > 0 ? "late" : "ready"} hint={report.missing_required.slice(0, 4).join(", ") || undefined} />
        <KpiCard label="Incremental sync" value={report.supports_incremental ? "supported" : "full only"} tone={report.supports_incremental ? "ready" : "neutral"} hint={report.supports_webhooks ? "webhooks supported" : "no webhooks (polling)"} />
      </div>
      <div className="row row-wrap gap-3">
        <SelectField label="Status" value={status} placeholder="All" options={statuses.map((s) => ({ value: s, label: s }))} onChange={setStatus} />
        <SelectField label="Importance" value={importance} placeholder="All" options={importances.map((s) => ({ value: s, label: s }))} onChange={setImportance} />
        <SelectField label="Entity" value={entity} placeholder="All" options={entities.map((s) => ({ value: s, label: s }))} onChange={setEntity} />
      </div>
      <DataTable rows={rows} columns={columns} rowKey={(a) => `${a.entity}.${a.field}`} rowClassName={(a) => (a.importance === "required" && a.status.toLowerCase() !== "available" ? "row-late" : a.status.toLowerCase() !== "available" ? "row-at-risk" : undefined)} initialSort={{ key: "importance", direction: "asc" }} dense pageSize={100} ariaLabel="ERP field capabilities" emptyMessage="No fields match" />
    </div>
  );
}

export function SyncPanel({ enabled }: { enabled: boolean }) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("runs");
  const [page, setPage] = useState(1);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [runDialog, setRunDialog] = useState(false);
  const [mode, setMode] = useState<SyncMode>("full");
  const [prune, setPrune] = useState(false);
  const now = useMemo(() => new Date(), []);

  const status = useSyncStatus(enabled);
  const runs = useSyncRuns({ page, page_size: 20 }, enabled);
  const capabilities = useSyncCapabilities(enabled);
  const runSync = useRunSync();

  const start = async () => {
    try {
      const r = await runSync.mutateAsync({ mode, prune_missing_orders: mode === "full" && prune });
      setRunDialog(false);
      toast.push({
        tone: r.status === "failed" ? "error" : r.issues_count > 0 ? "warning" : "success",
        title: `${humanize(r.mode)} sync ${r.status}`,
        message: `${formatNumber(sumCounts(r.records_fetched))} records fetched · ${formatNumber(sumCounts(r.records_upserted))} upserted · ${formatNumber(r.issues_count)} issues${r.reconciliation ? ` · reconciliation ${r.reconciliation.status}` : ""}`,
        durationMs: 9000,
      });
      setOpenRun(r.run_id);
    } catch (err) {
      toast.push({ tone: "error", title: "Sync failed", message: describeError(err) });
    }
  };

  const s = status.data;
  const lastRun = s?.last_run ?? null;

  return (
    <>
      <Section
        title="ERP synchronisation"
        count={s ? `${s.connector} · ${formatNumber(s.runs_total)} runs` : undefined}
        actions={
          enabled ? (
            <>
              <span className="text-xs text-faint">{lastRun ? `last run ${formatRelative(lastRun.started_at, now)} (${lastRun.status})` : "no run yet"}</span>
              <button type="button" className="btn btn-sm btn-primary" onClick={() => setRunDialog(true)} disabled={runSync.isPending}>
                {runSync.isPending ? "Syncing…" : "Run sync"}
              </button>
            </>
          ) : null
        }
      >
        {!enabled ? (
          <EmptyState compact title="Admin only" message="ERP synchronisation controls require the administrator role." />
        ) : (
          <div className="col gap-3">
            <Tabs<Tab>
              items={[
                { key: "runs", label: "Runs", count: runs.data?.meta.total },
                { key: "capabilities", label: "Field capabilities", count: capabilities.data?.assessments.length },
                { key: "connector", label: "Connector" },
              ]}
              value={tab}
              onChange={setTab}
              ariaLabel="Sync sections"
            />
            {tab === "runs" ? (
              runs.isPending ? (
                <LoadingState compact label="Loading sync runs" />
              ) : runs.isError ? (
                <ErrorState compact error={runs.error} onRetry={() => void runs.refetch()} />
              ) : runs.data.items.length === 0 ? (
                <EmptyState compact title="No sync runs yet" message="Run a full sync to load master data, orders and operations through the connector." />
              ) : (
                <>
                  <DataTable rows={runs.data.items} columns={runColumns} rowKey={(r) => r.run_id} onRowClick={(r) => setOpenRun(r.run_id)} rowClassName={(r) => (r.status === "failed" ? "row-late" : r.reconciliation?.status === "warning" ? "row-at-risk" : undefined)} initialSort={{ key: "started", direction: "desc" }} dense paginate={false} hideFooter ariaLabel="Sync runs" />
                  <Pager meta={runs.data.meta} page={page} shown={runs.data.items.length} busy={runs.isFetching} onPageChange={setPage} />
                </>
              )
            ) : null}
            {tab === "capabilities" ? (
              capabilities.isPending ? (
                <LoadingState compact label="Assessing connector fields" />
              ) : capabilities.isError ? (
                <ErrorState compact error={capabilities.error} onRetry={() => void capabilities.refetch()} />
              ) : (
                <CapabilitiesTab report={capabilities.data} />
              )
            ) : null}
            {tab === "connector" ? (
              status.isPending ? (
                <LoadingState compact label="Checking connector" />
              ) : status.isError ? (
                <ErrorState compact error={status.error} onRetry={() => void status.refetch()} />
              ) : s ? (
                <div className="grid grid-2" data-testid="sync-connector">
                  <dl className="kv">
                    <dt>Connector</dt>
                    <dd className="mono">{s.connector}</dd>
                    <dt>Health</dt>
                    <dd>
                      {s.health ? (
                        <>
                          <StatusPill tone={s.health.healthy ? "ready" : "late"} size="sm">
                            {s.health.healthy ? "healthy" : "unreachable"}
                          </StatusPill>{" "}
                          <span className="text-muted">{s.health.message}</span>
                          {s.health.latency_ms !== null ? <span className="text-faint num"> · {s.health.latency_ms.toFixed(1)} ms</span> : null}
                        </>
                      ) : (
                        "—"
                      )}
                    </dd>
                    <dt>Checked</dt>
                    <dd className="num">{formatDateTime(s.checked_at, "dd MMM HH:mm:ss")}</dd>
                    <dt>Watermark</dt>
                    <dd className="num">{s.watermark ? formatDateTime(s.watermark, "dd MMM yyyy HH:mm:ss") : "none (next incremental sync is a full fetch)"}</dd>
                    <dt>Runs</dt>
                    <dd className="num">{formatNumber(s.runs_total)}</dd>
                    {s.health && Object.keys(s.health.details).length > 0
                      ? Object.entries(s.health.details).map(([k, v]) => (
                          <div key={k} style={{ display: "contents" }}>
                            <dt className="mono">{k}</dt>
                            <dd className="num">{typeof v === "number" ? formatNumber(v) : String(v)}</dd>
                          </div>
                        ))
                      : null}
                  </dl>
                  <dl className="kv">
                    <dt>Last run</dt>
                    <dd>
                      {s.last_run ? (
                        <button type="button" className="btn btn-sm" onClick={() => setOpenRun(s.last_run?.run_id ?? null)}>
                          <StatusPill tone={syncStatusTone(s.last_run.status)} size="sm">
                            {s.last_run.status}
                          </StatusPill>
                          <span className="num">{formatDateTime(s.last_run.started_at, "dd MMM HH:mm")}</span>
                        </button>
                      ) : (
                        "—"
                      )}
                    </dd>
                    <dt>Last completed</dt>
                    <dd className="num">{s.last_completed ? `${formatDateTime(s.last_completed.finished_at ?? s.last_completed.started_at, "dd MMM HH:mm")} · ${s.last_completed.mode}` : "—"}</dd>
                  </dl>
                </div>
              ) : null
            ) : null}
          </div>
        )}
      </Section>

      <Modal
        open={runDialog}
        title="Run ERP synchronisation"
        onClose={() => setRunDialog(false)}
        footer={
          <>
            <button type="button" className="btn" onClick={() => setRunDialog(false)} disabled={runSync.isPending}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void start()} disabled={runSync.isPending} data-testid="sync-run-submit">
              {runSync.isPending ? "Syncing…" : `Run ${mode} sync`}
            </button>
          </>
        }
      >
        <div className="col gap-3">
          <p className="text-sm text-muted">Fetch → normalise → validate → upsert → reconcile. The ERP is only read; the run is recorded in sync_runs and every dashboard refreshes afterwards. A full sync of a large plant can take a minute.</p>
          <SelectField
            label="Mode"
            value={mode}
            options={[
              { value: "full", label: "Full — every entity" },
              { value: "incremental", label: `Incremental — changes since the watermark${capabilities.data && !capabilities.data.supports_incremental ? " (not supported by this connector)" : ""}` },
            ]}
            onChange={setMode}
            help={s?.watermark ? `Watermark ${formatDateTime(s.watermark, "dd MMM yyyy HH:mm")}` : "No watermark yet: an incremental run fetches everything."}
          />
          {mode === "full" ? <CheckField label="Prune orders missing from the ERP" checked={prune} onChange={setPrune} help="Marks open orders that the ERP no longer returns as cancelled (only in a full sync)." /> : null}
          {runSync.isError ? <div className="tone-late text-sm">{describeError(runSync.error)}</div> : null}
        </div>
      </Modal>

      <RunDetail runId={openRun} onClose={() => setOpenRun(null)} />
    </>
  );
}
