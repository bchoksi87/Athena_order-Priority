/**
 * Executive Dashboard (spec Phase 8 EXECUTIVE KPIs): every KPI of GET /analytics/kpis, the on-time
 * delivery trend and breakdowns, the top bottleneck, alert severities and the active plan card.
 * Read-only and auto-refreshing; the executive role lands here after login.
 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useAlertSummary } from "@/api/alerts";
import { useBottlenecks, useKpis, useOnTimeDelivery, useScheduleQuality } from "@/api/analytics";
import type { AlertSeverity, OtdBucket } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { BottleneckCard } from "@/components/BottleneckCard";
import { ComparisonTable } from "@/components/ComparisonTable";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { PlanVersionCard } from "@/components/PlanVersionCard";
import { Section } from "@/components/Section";
import { axisTick, chartColors, tooltipStyle } from "@/lib/chartTheme";
import { ALERT_SEVERITIES, humanize } from "@/lib/constants";
import { formatCurrency, formatNumber, formatPct } from "@/lib/formatters";
import { formatDateTime, formatRelative } from "@/lib/time";
import { useLocalStorage } from "@/lib/useLocalStorage";

import "./ExecutiveDashboard.css";

const OTD_WINDOWS = [7, 14, 30, 60, 90] as const;
const SEVERITY_TONE: Record<AlertSeverity, string> = { critical: "var(--sev-critical)", high: "var(--sev-high)", warning: "var(--sev-warning)", info: "var(--sev-info)" };

function pctTone(pct: number | null | undefined): "ready" | "at-risk" | "late" | "neutral" {
  if (pct === null || pct === undefined) return "neutral";
  if (pct >= 90) return "ready";
  if (pct >= 75) return "at-risk";
  return "late";
}

function utilTone(pct: number | null | undefined): "ready" | "at-risk" | "late" | "neutral" {
  if (pct === null || pct === undefined) return "neutral";
  if (pct > 100) return "late";
  if (pct >= 85) return "at-risk";
  return "ready";
}

/** Historical vs projected on-time % per bucket (customer tier or process). */
function OtdBreakdown({ historical, projected, label }: { historical: Record<string, OtdBucket>; projected: Record<string, OtdBucket>; label: string }) {
  const keys = Array.from(new Set([...Object.keys(historical), ...Object.keys(projected)]));
  if (keys.length === 0) return <div className="text-muted text-sm">No delivered or scheduled orders in this window.</div>;
  const rows = keys
    .map((k) => ({ key: k, h: historical[k] ?? null, p: projected[k] ?? null }))
    .sort((a, b) => (b.p?.total ?? 0) + (b.h?.total ?? 0) - ((a.p?.total ?? 0) + (a.h?.total ?? 0)));
  const bar = (b: OtdBucket | null) => (
    <>
      <span className="exec-otd-bar" aria-hidden="true">
        <span style={{ width: `${Math.max(0, Math.min(100, b?.pct ?? 0))}%`, background: `var(--status-${pctTone(b?.pct)})` }} />
      </span>
      <span className="num">{formatPct(b?.pct, 0)}</span>
      <span className="text-faint num"> {b ? `${formatNumber(b.on_time)}/${formatNumber(b.total)}` : ""}</span>
    </>
  );
  return (
    <table className="exec-otd-table" data-testid={`otd-by-${label}`}>
      <thead>
        <tr>
          <th>{label}</th>
          <th>Historical (delivered)</th>
          <th>Expected (scheduled)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <td className="strong">{humanize(r.key)}</td>
            <td>{bar(r.h)}</td>
            <td>{bar(r.p)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ExecutiveDashboardPage() {
  const navigate = useNavigate();
  const [autoRefresh, setAutoRefresh] = useLocalStorage<boolean>("ppse.exec.autoRefresh", true);
  const [windowDays, setWindowDays] = useState<number>(30);
  const now = useMemo(() => new Date(), []);

  const kpis = useKpis(autoRefresh ? 60_000 : false);
  const otd = useOnTimeDelivery({ window_days: windowDays });
  const quality = useScheduleQuality(autoRefresh ? 120_000 : false);
  const bottlenecks = useBottlenecks(autoRefresh ? 120_000 : false);
  const alerts = useAlertSummary(autoRefresh ? 60_000 : false);

  const k = kpis.data?.kpis;
  const report = kpis.data;

  const trend = useMemo(
    () =>
      (otd.data?.trend ?? []).map((p) => ({
        day: formatDateTime(`${p.day}T00:00:00`, "dd MMM"),
        historical: p.historical?.pct ?? null,
        projected: p.projected?.pct ?? null,
        delivered: p.historical?.total ?? 0,
        scheduled: p.projected?.total ?? 0,
      })),
    [otd.data],
  );
  const qualityComponents = useMemo(
    () => Object.entries(quality.data?.quality?.components ?? {}).map(([name, value]) => ({ name: humanize(name), value, weight: quality.data?.quality?.weights[name] ?? 0 })),
    [quality.data],
  );
  const bySeverity = alerts.data?.by_severity ?? {};

  const refreshAll = () => {
    void Promise.all([kpis.refetch(), otd.refetch(), quality.refetch(), bottlenecks.refetch(), alerts.refetch()]);
  };
  const busy = kpis.isFetching || quality.isFetching || bottlenecks.isFetching;

  return (
    <div className="page" data-testid="executive-dashboard">
      <PageHeader
        eyebrow="Analyse"
        title="Executive Dashboard"
        subtitle={
          report ? (
            <span>
              As of {formatDateTime(report.as_of)} ({formatRelative(report.as_of, now)}) · {report.source === "stored" ? "computed with" : "recomputed live against"} plan{" "}
              {report.version_number !== null ? `v${report.version_number} (${humanize(report.version_status)})` : "— no schedule yet"}
            </span>
          ) : (
            "Delivery performance, exposure, capacity and the active plan at a glance."
          )
        }
        actions={
          <>
            <label className="row gap-1 text-sm text-muted">
              <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              Auto-refresh
            </label>
            <button type="button" className="btn btn-sm" onClick={refreshAll} disabled={busy}>
              {busy ? "Refreshing…" : "Refresh"}
            </button>
          </>
        }
      />

      <Section title="Delivery" count={k ? `${formatNumber(k.total_open_orders)} open orders` : undefined}>
        <div className="grid grid-kpi" data-testid="kpi-delivery">
          <KpiCard label="Total open orders" value={formatNumber(k?.total_open_orders)} tone="neutral" loading={kpis.isPending} onClick={() => navigate(routes.priorityQueue)} hint="click for the priority queue" />
          <KpiCard label="Total pending quantity" value={formatNumber(k?.total_pending_quantity)} unit="units" tone="neutral" loading={kpis.isPending} />
          <KpiCard label="Orders due today" value={formatNumber(k?.orders_due_today)} tone={(k?.orders_due_today ?? 0) > 0 ? "at-risk" : "neutral"} loading={kpis.isPending} />
          <KpiCard label="Orders due tomorrow" value={formatNumber(k?.orders_due_tomorrow)} tone="neutral" loading={kpis.isPending} />
          <KpiCard label="Orders due this week" value={formatNumber(k?.orders_due_this_week)} tone="neutral" loading={kpis.isPending} />
          <KpiCard label="Overdue orders" value={formatNumber(k?.overdue_orders)} tone={(k?.overdue_orders ?? 0) > 0 ? "late" : "ready"} loading={kpis.isPending} onClick={() => navigate(`${routes.priorityQueue}?due_to=${formatDateTime(now, "yyyy-MM-dd")}`)} hint="due date in the past" />
          <KpiCard label="At-risk orders" value={formatNumber(k?.at_risk_orders)} tone={(k?.at_risk_orders ?? 0) > 0 ? "blocked" : "ready"} loading={kpis.isPending} onClick={() => navigate(`${routes.priorityQueue}?risk=high`)} hint="risk high or critical" />
          <KpiCard label="On-time delivery %" value={formatPct(k?.on_time_delivery_pct, 0)} tone={pctTone(k?.on_time_delivery_pct)} loading={kpis.isPending} hint={otd.data ? `delivered in the last ${otd.data.window_days} days` : "historical"} sparkline={trend.map((t) => t.historical).filter((v): v is number => v !== null)} />
          <KpiCard label="Expected on-time %" value={formatPct(k?.expected_on_time_delivery_pct, 0)} tone={pctTone(k?.expected_on_time_delivery_pct)} loading={kpis.isPending} hint="scheduled open orders in the plan" />
        </div>
      </Section>

      <Section title="Capacity & exposure">
        <div className="grid grid-kpi" data-testid="kpi-capacity">
          <KpiCard label="Machine utilisation" value={formatPct(k?.machine_utilization_pct, 0)} tone={utilTone(k?.machine_utilization_pct)} loading={kpis.isPending} hint="planned busy time over the horizon" onClick={() => navigate(routes.machineSchedule)} />
          <KpiCard label="Capacity utilisation" value={formatPct(k?.capacity_utilization_pct, 0)} tone={utilTone(k?.capacity_utilization_pct)} loading={kpis.isPending} hint="required ÷ available hours" onClick={() => navigate(routes.capacity)} />
          <KpiCard label="Revenue at risk" value={formatCurrency(k?.revenue_at_risk)} tone={(k?.revenue_at_risk ?? 0) > 0 ? "late" : "ready"} loading={kpis.isPending} hint="order value of late / at-risk orders" />
          <KpiCard label="Margin at risk" value={formatCurrency(k?.margin_at_risk)} tone={(k?.margin_at_risk ?? 0) > 0 ? "late" : "ready"} loading={kpis.isPending} />
          <KpiCard label="Scheduled orders" value={formatNumber(k?.scheduled_orders)} tone="running" loading={kpis.isPending} onClick={() => navigate(routes.gantt)} />
          <KpiCard label="Unscheduled orders" value={formatNumber(k?.unscheduled_orders)} tone={(k?.unscheduled_orders ?? 0) > 0 ? "at-risk" : "ready"} loading={kpis.isPending} hint="not placed in the horizon" onClick={() => navigate(routes.controlTower)} />
        </div>
      </Section>

      <div className="grid grid-2">
        <Section
          title="On-time delivery trend"
          count={otd.data ? `${otd.data.window_days} days` : undefined}
          actions={
            <select className="select" value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))} aria-label="OTD window">
              {OTD_WINDOWS.map((w) => (
                <option key={w} value={w}>
                  last {w} days
                </option>
              ))}
            </select>
          }
        >
          <AsyncContent query={otd} isEmpty={(r) => r.trend.length === 0} emptyTitle="No delivery history" emptyMessage="No delivered or scheduled orders in this window." compact>
            {(r) => (
              <div className="col gap-2">
                <div className="text-sm">{r.summary}</div>
                <div className="chart-box" style={{ height: 220 }}>
                  <ResponsiveContainer>
                    <LineChart data={trend} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
                      <CartesianGrid stroke={chartColors.grid} vertical={false} />
                      <XAxis dataKey="day" tick={axisTick} axisLine={false} tickLine={false} minTickGap={24} />
                      <YAxis domain={[0, 100]} tick={axisTick} axisLine={false} tickLine={false} unit="%" />
                      <Tooltip contentStyle={tooltipStyle} formatter={(v, name) => [v === null || v === undefined ? "—" : `${Number(v).toFixed(1)}%`, name === "historical" ? "Delivered on time" : "Expected on time"]} />
                      <Legend wrapperStyle={{ fontSize: 11 }} formatter={(v) => (v === "historical" ? "Historical (delivered)" : "Expected (scheduled)")} />
                      <Line type="monotone" dataKey="historical" stroke="var(--cat-1)" strokeWidth={2} dot={{ r: 2.5 }} connectNulls isAnimationActive={false} />
                      <Line type="monotone" dataKey="projected" stroke="var(--cat-3)" strokeWidth={2} strokeDasharray="5 3" dot={{ r: 2.5 }} connectNulls isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                {Object.values(r.notes).some((n) => n > 0) ? (
                  <div className="text-xs text-faint">
                    Not counted:{" "}
                    {Object.entries(r.notes)
                      .filter(([, n]) => n > 0)
                      .map(([key, n]) => `${n} ${humanize(key).toLowerCase()}`)
                      .join(" · ")}
                  </div>
                ) : null}
              </div>
            )}
          </AsyncContent>
        </Section>

        <div className="col gap-3">
          <AsyncContent query={quality} emptyTitle="No plan" compact isEmpty={() => false}>
            {(q) => (
              <PlanVersionCard version={q.version} quality={q.quality} title={q.version ? `Active plan · ${humanize(q.version.status)}` : "Active plan"}>
                {q.newest_draft && q.comparison ? (
                  <div className="col gap-1">
                    <div className="text-xs text-muted upper">Newer draft v{q.newest_draft.version_number} vs active</div>
                    <ComparisonTable metrics={q.comparison.metrics} quality={q.comparison.quality} beforeLabel={`v${q.comparison.a.version_number}`} afterLabel={`v${q.comparison.b.version_number}`} dense />
                  </div>
                ) : null}
              </PlanVersionCard>
            )}
          </AsyncContent>
          <Section title="Schedule quality components" actions={quality.data?.quality ? <span className="text-faint text-xs">weighted score {formatNumber(quality.data.quality.score, 1)}</span> : null}>
            <AsyncContent query={quality} isEmpty={(q) => !q.quality} emptyTitle="No schedule yet" emptyMessage="Quality is computed for every generated schedule." compact>
              {() => (
                <div className="chart-box" style={{ height: Math.max(140, qualityComponents.length * 26 + 20) }}>
                  <ResponsiveContainer>
                    <BarChart data={qualityComponents} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 24 }} barSize={12}>
                      <CartesianGrid stroke={chartColors.grid} horizontal={false} />
                      <XAxis type="number" domain={[0, 100]} tick={axisTick} axisLine={false} tickLine={false} />
                      <YAxis type="category" dataKey="name" tick={axisTick} axisLine={false} tickLine={false} width={120} />
                      <Tooltip contentStyle={tooltipStyle} formatter={(v, _n, item) => [`${Number(v ?? 0).toFixed(0)} · weight ${((item.payload as { weight: number }).weight * 100).toFixed(0)}%`, "component score"]} />
                      <Bar dataKey="value" fill={chartColors.primary} radius={[0, 3, 3, 0]} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </AsyncContent>
          </Section>
        </div>
      </div>

      <div className="grid grid-3">
        <Section title="Blocked orders" count={k ? formatNumber(k.blocked_total) : undefined}>
          <div className="grid grid-2" data-testid="kpi-blocked">
            <KpiCard label="Blocked by material" value={formatNumber(k?.blocked_by_material)} tone={(k?.blocked_by_material ?? 0) > 0 ? "blocked" : "ready"} loading={kpis.isPending} onClick={() => navigate(`${routes.priorityQueue}?readiness=waiting_material`)} />
            <KpiCard label="Blocked by tooling" value={formatNumber(k?.blocked_by_tooling)} tone={(k?.blocked_by_tooling ?? 0) > 0 ? "blocked" : "ready"} loading={kpis.isPending} onClick={() => navigate(`${routes.priorityQueue}?readiness=waiting_tooling`)} />
            <KpiCard label="Blocked by machine" value={formatNumber(k?.blocked_by_machine)} tone={(k?.blocked_by_machine ?? 0) > 0 ? "blocked" : "ready"} loading={kpis.isPending} onClick={() => navigate(`${routes.priorityQueue}?readiness=machine_unavailable`)} />
            <KpiCard label="Waiting for approval" value={formatNumber(k?.waiting_for_approval)} tone={(k?.waiting_for_approval ?? 0) > 0 ? "hold" : "ready"} loading={kpis.isPending} onClick={() => navigate(`${routes.priorityQueue}?readiness=waiting_approval`)} />
          </div>
          <div className="text-xs text-faint mt-2">
            {k ? `${formatNumber(k.blocked_total)} orders blocked in total (${formatPct(k.total_open_orders > 0 ? (100 * k.blocked_total) / k.total_open_orders : null, 0)} of open orders)` : ""}
          </div>
        </Section>
        <AsyncContent query={bottlenecks} emptyTitle="No bottleneck data" compact isEmpty={() => false}>
          {(r) => <BottleneckCard bottleneck={r.current} title="Top bottleneck" onOpen={() => navigate(routes.bottlenecks)} openLabel="Bottleneck analysis" compact />}
        </AsyncContent>
        <Section title="Open alerts" count={alerts.data ? `${formatNumber(alerts.data.total_active)} active · ${formatNumber(alerts.data.unacknowledged)} unacknowledged` : undefined} actions={<button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(routes.alerts)}>All alerts</button>}>
          <AsyncContent query={alerts} emptyTitle="No alerts" compact isEmpty={() => false}>
            {() => (
              <div className="exec-sev" data-testid="alert-severities">
                {ALERT_SEVERITIES.map((sev) => (
                  <button key={sev} type="button" className="exec-sev-tile" style={{ borderTopColor: SEVERITY_TONE[sev] }} onClick={() => navigate(`${routes.alerts}?severity=${sev}`)}>
                    <span className="label">{sev}</span>
                    <span className="num">{formatNumber(bySeverity[sev] ?? 0)}</span>
                  </button>
                ))}
              </div>
            )}
          </AsyncContent>
        </Section>
      </div>

      <div className="grid grid-2">
        <Section title="On-time delivery by customer tier">
          <AsyncContent query={otd} emptyTitle="No data" compact isEmpty={() => false}>
            {(r) => <OtdBreakdown historical={r.historical_by_tier} projected={r.projected_by_tier} label="tier" />}
          </AsyncContent>
        </Section>
        <Section title="On-time delivery by process">
          <AsyncContent query={otd} emptyTitle="No data" compact isEmpty={() => false}>
            {(r) => <OtdBreakdown historical={r.historical_by_process} projected={r.projected_by_process} label="process" />}
          </AsyncContent>
        </Section>
      </div>
    </div>
  );
}
