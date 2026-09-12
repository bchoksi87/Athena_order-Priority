import { useMemo } from "react";

import { describeError } from "@/api/client";
import { useSchedulingConfig } from "@/api/config";
import { usePriorityProfile } from "@/api/priority";
import { useScheduleVersions } from "@/api/schedule";
import { useHealth, useRunSync, useSyncRuns } from "@/api/system";
import type { ScheduleVersion, SyncRun } from "@/api/types";
import { useAuth } from "@/app/auth";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { ScheduleStatusPill, StatusPill } from "@/components/StatusPill";
import { useToast } from "@/components/Toast";
import { ROLE_LABELS, WRITEBACK_LABELS } from "@/lib/constants";
import { formatScore } from "@/lib/formatters";
import { formatDateTime, formatRelative } from "@/lib/time";

const syncColumns: Column<SyncRun>[] = [
  { key: "started", header: "Started", cell: (r) => <span className="num">{formatDateTime(r.started_at, "dd MMM HH:mm:ss")}</span>, sortValue: (r) => r.started_at },
  { key: "mode", header: "Mode", cell: (r) => r.mode, sortValue: (r) => r.mode },
  { key: "status", header: "Status", cell: (r) => <StatusPill tone={r.status === "succeeded" || r.status === "success" ? "ready" : r.status === "failed" ? "late" : "running"} size="sm">{r.status}</StatusPill>, sortValue: (r) => r.status },
  { key: "finished", header: "Finished", cell: (r) => <span className="num">{formatDateTime(r.finished_at, "HH:mm:ss")}</span> },
  { key: "fetched", header: "Fetched", numeric: true, cell: (r) => r.records_fetched ?? "—" },
  { key: "upserted", header: "Upserted", numeric: true, cell: (r) => r.records_upserted ?? "—" },
  { key: "errors", header: "Errors", cell: (r) => (r.errors?.length ? <span className="tone-late">{r.errors.length}: {r.errors[0]}</span> : r.message ?? "—") },
];

const versionColumns: Column<ScheduleVersion>[] = [
  { key: "v", header: "Version", numeric: true, cell: (v) => <span className="strong">v{v.version_number}</span>, sortValue: (v) => v.version_number },
  { key: "status", header: "Status", cell: (v) => <ScheduleStatusPill status={v.status} size="sm" />, sortValue: (v) => v.status },
  { key: "gen", header: "Generated", cell: (v) => <span className="num">{formatDateTime(v.generated_at)}</span>, sortValue: (v) => v.generated_at },
  { key: "by", header: "By", cell: (v) => v.generated_by ?? "system" },
  { key: "algo", header: "Algorithm", cell: (v) => <span className="mono">{v.algorithm} {v.algorithm_version}</span> },
  { key: "cfg", header: "Profile / config", cell: (v) => <span className="mono text-muted">{v.profile_id} v{v.profile_version} · cfg v{v.config_version}</span> },
  { key: "q", header: "Quality", numeric: true, cell: (v) => formatScore(v.quality_score) },
  { key: "note", header: "Note", cell: (v) => v.note ?? "" },
];

/** Health, ERP sync control, configuration versions and schedule versions. Admin only. */
export default function SystemAdministrationPage() {
  const toast = useToast();
  const { user } = useAuth();
  const health = useHealth(15_000);
  const syncRuns = useSyncRuns();
  const runSync = useRunSync();
  const versions = useScheduleVersions();
  const profile = usePriorityProfile();
  const config = useSchedulingConfig();
  const now = useMemo(() => new Date(), []);
  const lastRun = syncRuns.data?.[0];

  const onSync = async (mode: "full" | "incremental") => {
    try {
      const r = await runSync.mutateAsync(mode);
      toast.push({ tone: "success", title: `${mode} sync ${r.status}`, message: r.message ?? undefined });
    } catch (err) {
      toast.push({ tone: "error", title: "Sync failed", message: describeError(err) });
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="System"
        title="System Administration"
        subtitle={`Signed in as ${user?.display_name ?? "—"} (${user ? ROLE_LABELS[user.role] : "—"})`}
        actions={
          <>
            <button type="button" className="btn" onClick={() => void onSync("incremental")} disabled={runSync.isPending}>
              Incremental sync
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void onSync("full")} disabled={runSync.isPending}>
              {runSync.isPending ? "Syncing…" : "Full ERP sync"}
            </button>
          </>
        }
      />
      <div className="grid grid-kpi">
        <KpiCard label="API" value={health.data?.status ?? (health.isError ? "down" : "…")} tone={health.data?.status === "ok" ? "ready" : "late"} hint={health.data ? `v${health.data.version} · ${health.data.environment}` : undefined} loading={health.isPending} />
        <KpiCard label="Database" value={health.data?.database ?? "—"} tone={health.data?.database === "ok" ? "ready" : "late"} loading={health.isPending} />
        <KpiCard label="Writeback mode" value={WRITEBACK_LABELS.read_only} tone="at-risk" hint="ERP is never written in read-only mode" />
        <KpiCard label="Last sync" value={lastRun ? formatRelative(lastRun.started_at, now) : "never"} tone={lastRun?.status === "failed" ? "late" : "neutral"} hint={lastRun ? `${lastRun.mode} · ${lastRun.status}` : undefined} loading={syncRuns.isPending} />
        <KpiCard label="Priority profile" value={profile.data ? `v${profile.data.version}` : "—"} tone="neutral" hint={profile.data?.profile_id} loading={profile.isPending} />
        <KpiCard label="Scheduling config" value={config.data ? `v${config.data.version}` : "—"} tone="neutral" hint={config.data ? `${config.data.algorithm} · ${config.data.horizon_days}d horizon` : undefined} loading={config.isPending} />
        <KpiCard label="Schedule versions" value={versions.data?.length ?? "—"} tone="neutral" loading={versions.isPending} />
      </div>
      <Section title="ERP sync runs" count={syncRuns.data?.length} flush>
        <AsyncContent query={syncRuns} emptyTitle="No sync runs yet" emptyMessage="Trigger a full sync to load master data through the connector." compact>
          {(rows) => <DataTable rows={rows} columns={syncColumns} rowKey={(r) => r.run_id} initialSort={{ key: "started", direction: "desc" }} dense pageSize={20} />}
        </AsyncContent>
      </Section>
      <Section title="Schedule versions" count={versions.data?.length} flush>
        <AsyncContent query={versions} emptyTitle="No schedule versions" compact>
          {(rows) => <DataTable rows={rows} columns={versionColumns} rowKey={(v) => String(v.version_number)} initialSort={{ key: "v", direction: "desc" }} dense pageSize={25} />}
        </AsyncContent>
      </Section>
      <Section title="Users & roles">
        <p className="text-muted text-sm">User management is seeded by the backend (<span className="mono">python -m app.cli seed</span>). Role precedence: executive (read-only) &lt; operator &lt; supervisor &lt; planner &lt; production manager &lt; admin.</p>
        <dl className="kv">
          {(Object.keys(ROLE_LABELS) as Array<keyof typeof ROLE_LABELS>).map((r) => (
            <div key={r} style={{ display: "contents" }}>
              <dt className="mono">{r}</dt>
              <dd>{ROLE_LABELS[r]}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </div>
  );
}
