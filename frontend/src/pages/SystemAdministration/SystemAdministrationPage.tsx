/**
 * System Administration (admin): environment and health from GET /health, in-process metrics,
 * user accounts, ERP synchronisation (runs, capabilities, connector) and links to the configuration screens.
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { useSchedulingConfiguration } from "@/api/config";
import { usePriorityConfiguration } from "@/api/priority";
import { useHealth, useMetrics } from "@/api/system";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { StatusPill } from "@/components/StatusPill";
import { ROLES, ROLE_LABELS, WRITEBACK_LABELS, humanize, syncStatusTone } from "@/lib/constants";
import { formatNumber, formatScore } from "@/lib/formatters";
import { formatDateTime, formatRelative } from "@/lib/time";

import { SyncPanel } from "./SyncPanel";
import { UsersPanel } from "./UsersPanel";

const APP_ENV = typeof import.meta.env.VITE_APP_ENV === "string" ? import.meta.env.VITE_APP_ENV : "dev";

export default function SystemAdministrationPage() {
  const { user, hasMinRole } = useAuth();
  const isAdmin = hasMinRole("admin");
  const now = useMemo(() => new Date(), []);
  const health = useHealth(15_000);
  const metrics = useMetrics(30_000);
  const priority = usePriorityConfiguration();
  const scheduling = useSchedulingConfiguration();

  const h = health.data;
  const m = metrics.data;
  const apiTone = h ? (h.status === "ok" ? "ready" : "at-risk") : health.isError ? "late" : "neutral";
  const connectorTone = h?.connector ? (h.connector.reachable ? "ready" : "late") : "neutral";
  const lastSyncTone = h?.last_sync ? syncStatusTone(h.last_sync.status) : "neutral";

  return (
    <div className="page" data-testid="system-administration-page">
      <PageHeader
        eyebrow="System"
        title="System Administration"
        subtitle={
          <span className="row row-wrap">
            <span>
              Signed in as <strong>{user?.display_name ?? "—"}</strong> ({user ? ROLE_LABELS[user.role] : "—"})
            </span>
            {h ? (
              <span className="text-faint">
                API v{h.version} · {h.environment} · server time {formatDateTime(h.time, "HH:mm:ss")}
              </span>
            ) : null}
          </span>
        }
        actions={
          <button type="button" className="btn btn-sm" onClick={() => void Promise.all([health.refetch(), metrics.refetch()])} disabled={health.isFetching}>
            {health.isFetching ? "Refreshing…" : "Refresh"}
          </button>
        }
      />

      <div className="grid grid-kpi" data-testid="admin-health">
        <KpiCard label="API" value={h?.status ?? (health.isError ? "down" : "…")} tone={apiTone} hint={h ? `v${h.version} · ${h.environment}` : "GET /health failed"} loading={health.isPending} />
        <KpiCard label="Database" value={h?.database ?? "—"} tone={h?.database === "ok" ? "ready" : h ? "late" : "neutral"} hint={h ? `checked ${formatDateTime(h.time, "HH:mm:ss")}` : undefined} loading={health.isPending} />
        <KpiCard label="ERP connector" value={h?.connector ? (h.connector.reachable ? "reachable" : "unreachable") : "—"} tone={connectorTone} hint={h?.connector ? `${h.connector.name}${h.connector.latency_ms !== null ? ` · ${h.connector.latency_ms.toFixed(1)} ms` : ""}` : "not reported"} loading={health.isPending} title={h?.connector?.message} />
        <KpiCard label="Last ERP sync" value={h?.last_sync ? formatRelative(h.last_sync.started_at, now) : "never"} tone={lastSyncTone} hint={h?.last_sync ? `${h.last_sync.mode} · ${h.last_sync.status}` : undefined} loading={health.isPending} />
        <KpiCard label="Active plan" value={h?.active_plan ? `v${h.active_plan.version_number}` : "none"} tone={h?.active_plan ? (h.active_plan.status === "published" ? "ready" : "at-risk") : "neutral"} hint={h?.active_plan ? `${h.active_plan.status} · quality ${h.active_plan.quality_score !== null ? formatScore(h.active_plan.quality_score) : "—"} · ${formatRelative(h.active_plan.generated_at, now)}` : "generate a schedule"} loading={health.isPending} />
        <KpiCard label="Background jobs" value={h ? (h.background_jobs.running ? "running" : h.background_jobs.enabled ? "idle" : "disabled") : "—"} tone={h ? (h.background_jobs.running ? "running" : h.background_jobs.enabled ? "neutral" : "at-risk") : "neutral"} hint={h ? `${h.background_jobs.jobs.length} scheduled job(s)` : undefined} loading={health.isPending} />
        <KpiCard label="Writeback mode" value={h ? (WRITEBACK_LABELS[h.writeback_mode] ?? h.writeback_mode) : "—"} tone={h ? (h.writeback_mode === "read_only" ? "at-risk" : "late") : "neutral"} hint={h && h.writeback_mode !== "read_only" ? "deployment setting (PPSE_WRITEBACK_MODE); publishing writes to the ERP through the gateway" : "deployment setting (PPSE_WRITEBACK_MODE); the ERP is never written in read-only mode"} loading={health.isPending} />
        <KpiCard label="Requests" value={m ? formatNumber(m.requests_total) : "—"} tone={m && m.errors_total > 0 ? "at-risk" : "neutral"} hint={m ? `${formatNumber(m.errors_total)} errors (5xx) · ${formatNumber(m.active_alerts_total)} active alerts` : undefined} loading={metrics.isPending} />
      </div>

      <div className="grid grid-main-side">
        <div className="col gap-3">
          <SyncPanel enabled={isAdmin} />
          <UsersPanel enabled={isAdmin} />
        </div>

        <div className="col gap-3">
          <Section title="Configuration">
            <div className="col gap-1 text-sm">
              <Link to={routes.priorityConfig} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Priority configuration</span>
                <span className="text-faint">{priority.data ? `${priority.data.profile.name} · v${priority.data.profile.version}` : ""}</span>
              </Link>
              <Link to={routes.schedulingConfig} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Scheduling configuration</span>
                <span className="text-faint">{scheduling.data ? `${scheduling.data.scheduling.algorithm} · ${scheduling.data.scheduling.horizon_days}d horizon · v${scheduling.data.scheduling.version}` : ""}</span>
              </Link>
              <Link to={routes.dataQuality} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Data quality</span>
              </Link>
              <Link to={routes.audit} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Audit log</span>
              </Link>
              <Link to={routes.alerts} className="btn" style={{ justifyContent: "space-between" }}>
                <span>Alerts</span>
                <span className="text-faint">{m ? `${formatNumber(m.active_alerts_total)} active` : ""}</span>
              </Link>
            </div>
          </Section>

          <Section title="Engine activity" count={m ? "in-process counters" : undefined}>
            {m ? (
              <dl className="kv">
                <dt>Schedule generations</dt>
                <dd className="num">{formatNumber(m.schedule_generations_total)}</dd>
                <dt>Replans</dt>
                <dd className="num">{formatNumber(m.replans_total)}</dd>
                <dt>Failed runs</dt>
                <dd className={`num ${m.failed_runs_total > 0 ? "tone-late" : ""}`}>{formatNumber(m.failed_runs_total)}</dd>
                <dt>Sync runs</dt>
                <dd className="num">
                  {formatNumber(m.sync_runs_total)}
                  {m.sync_runs_failed_total > 0 ? <span className="tone-late"> · {formatNumber(m.sync_runs_failed_total)} failed</span> : null}
                </dd>
                <dt>Active plan</dt>
                <dd className="num">{m.active_plan_version !== null ? `v${m.active_plan_version}` : "—"}</dd>
                <dt>Versions</dt>
                <dd className="row row-wrap gap-1">
                  {Object.entries(m.schedule_versions_by_status)
                    .filter(([, n]) => n > 0)
                    .map(([k, n]) => (
                      <span key={k} className="chip">
                        {humanize(k)} <span className="num">{n}</span>
                      </span>
                    ))}
                </dd>
                <dt>HTTP</dt>
                <dd className="row row-wrap gap-1">
                  {Object.entries(m.by_status).map(([k, v]) => (
                    <span key={k} className={`chip${k.startsWith("5") ? " tone-late" : ""}`}>
                      {k} <span className="num">{formatNumber(v)}</span>
                    </span>
                  ))}
                </dd>
              </dl>
            ) : metrics.isError ? (
              <span className="text-muted text-sm">Metrics unavailable.</span>
            ) : (
              <span className="text-muted text-sm">Loading…</span>
            )}
          </Section>

          <Section title="Background jobs" count={h ? (h.background_jobs.enabled ? (h.background_jobs.running ? "running" : "enabled") : "disabled") : undefined}>
            {h && h.background_jobs.jobs.length > 0 ? (
              <table className="fld-map">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Trigger</th>
                    <th>Next run</th>
                  </tr>
                </thead>
                <tbody>
                  {h.background_jobs.jobs.map((j) => (
                    <tr key={j.id}>
                      <td>{j.name}</td>
                      <td className="mono text-muted">{j.trigger}</td>
                      <td className="num">{j.next_run_time ? formatDateTime(j.next_run_time, "dd MMM HH:mm") : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="text-sm text-muted">{h?.background_jobs.enabled ? "No jobs scheduled in this process." : "The in-process scheduler is disabled; syncs and replans run from the worker or on demand."}</p>
            )}
          </Section>

          <Section title="Roles">
            <p className="text-muted text-xs">Precedence for "role or higher": executive (read-only) &lt; operator &lt; supervisor &lt; planner &lt; production manager &lt; admin. Executives may read every dashboard but never write.</p>
            <dl className="kv">
              {ROLES.map((r) => (
                <div key={r} style={{ display: "contents" }}>
                  <dt className="mono">{r}</dt>
                  <dd>
                    {ROLE_LABELS[r]}
                    {user?.role === r ? (
                      <StatusPill tone="running" size="sm" dot={false}>
                        you
                      </StatusPill>
                    ) : null}
                  </dd>
                </div>
              ))}
            </dl>
            <div className="text-xs text-faint mt-2">UI build: {APP_ENV}</div>
          </Section>
        </div>
      </div>
    </div>
  );
}
