import { addDays, startOfDay } from "date-fns";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useSchedulingConfig } from "@/api/config";
import { useMachines } from "@/api/machines";
import { useApproveSchedule, useGenerateSchedule, usePublishSchedule, useSchedule } from "@/api/schedule";
import type { ScheduleEntry, TimeWindow } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { describeError } from "@/api/client";
import { AsyncContent } from "@/components/AsyncContent";
import { GanttChart, type GanttColorMode, type GanttRow } from "@/components/GanttChart";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { ScheduleStatusPill } from "@/components/StatusPill";
import { useToast } from "@/components/Toast";
import { Toolbar, ToolbarGroup, ToolbarSpacer } from "@/components/Toolbar";
import { formatHours, formatMinutes, formatPct, formatScore } from "@/lib/formatters";
import { formatDateTime, parseUtc } from "@/lib/time";
import type { GanttZoom } from "@/lib/timeScale";

const ZOOM_DAYS: Record<GanttZoom, number> = { day: 1, week: 7, fortnight: 14 };

/** Interactive Gantt of the current schedule with version actions. */
export default function GanttSchedulePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { hasMinRole } = useAuth();
  const schedule = useSchedule();
  const machines = useMachines();
  const config = useSchedulingConfig();
  const generate = useGenerateSchedule();
  const approve = useApproveSchedule();
  const publish = usePublishSchedule();

  const [zoom, setZoom] = useState<GanttZoom>("week");
  const [colorBy, setColorBy] = useState<GanttColorMode>("status");
  const [group, setGroup] = useState("");
  const [offsetDays, setOffsetDays] = useState(0);
  const [selected, setSelected] = useState<ScheduleEntry | null>(null);
  const now = useMemo(() => new Date(), []);

  const horizonStart = parseUtc(schedule.data?.horizon_start) ?? startOfDay(now);
  const windowStart = addDays(startOfDay(horizonStart), offsetDays);
  const windowEnd = addDays(windowStart, ZOOM_DAYS[zoom]);

  const groups = useMemo(() => Array.from(new Set((machines.data ?? []).map((m) => m.machine_group))).sort(), [machines.data]);

  const rows = useMemo<GanttRow[]>(() => {
    const list = machines.data ?? [];
    if (list.length > 0) {
      return list
        .filter((m) => !group || m.machine_group === group)
        .sort((a, b) => a.machine_group.localeCompare(b.machine_group) || a.preferred_rank - b.preferred_rank || a.machine_id.localeCompare(b.machine_id))
        .map((m) => ({ id: m.machine_id, label: m.machine_name || m.machine_id, sublabel: `${m.machine_group} · ${m.status}` }));
    }
    const ids = new Set((schedule.data?.entries ?? []).map((e) => e.machine_id));
    return Array.from(ids)
      .sort()
      .map((id) => ({ id, label: id }));
  }, [machines.data, schedule.data, group]);

  const downtime = useMemo(() => {
    const out: Record<string, TimeWindow[]> = {};
    for (const m of machines.data ?? []) out[m.machine_id] = [...m.maintenance_windows, ...m.planned_downtime, ...m.unplanned_downtime];
    return out;
  }, [machines.data]);

  const version = schedule.data?.version ?? null;
  const metrics = schedule.data?.metrics;

  const runAction = async (label: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
      toast.push({ tone: "success", title: `${label} succeeded` });
    } catch (err) {
      toast.push({ tone: "error", title: `${label} failed`, message: describeError(err) });
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="Plan"
        title="Gantt Schedule"
        subtitle={
          version ? (
            <span>
              Schedule v{version.version_number} <ScheduleStatusPill status={version.status} size="sm" /> · {version.algorithm} {version.algorithm_version} ·{" "}
              {version.profile_id} v{version.profile_version} · generated {formatDateTime(version.generated_at)}
              {version.generated_by ? ` by ${version.generated_by}` : ""}
            </span>
          ) : (
            "No schedule version yet — generate one to populate the chart."
          )
        }
        actions={
          <>
            {hasMinRole("planner") ? (
              <button type="button" className="btn btn-primary" disabled={generate.isPending} onClick={() => void runAction("Generate", () => generate.mutateAsync({}))}>
                {generate.isPending ? "Generating…" : "Generate schedule"}
              </button>
            ) : null}
            {hasMinRole("production_manager") && version && version.status === "draft" ? (
              <button type="button" className="btn" disabled={approve.isPending} onClick={() => void runAction("Approve", () => approve.mutateAsync({ version_number: version.version_number }))}>
                Approve v{version.version_number}
              </button>
            ) : null}
            {hasMinRole("production_manager") && version && version.status === "approved" ? (
              <button type="button" className="btn" disabled={publish.isPending} onClick={() => void runAction("Publish", () => publish.mutateAsync({ version_number: version.version_number }))}>
                Publish v{version.version_number}
              </button>
            ) : null}
          </>
        }
      />

      <div className="grid grid-kpi">
        <KpiCard label="Quality" value={formatScore(schedule.data?.quality?.score)} unit="/100" tone="running" hint={schedule.data?.quality?.summary} loading={schedule.isPending} />
        <KpiCard label="On time" value={formatPct(metrics?.on_time_pct, 0)} tone="ready" hint={`${metrics?.on_time_orders ?? "—"} of ${metrics?.scheduled_orders ?? "—"} orders`} loading={schedule.isPending} />
        <KpiCard label="Late" value={metrics?.late_orders ?? "—"} tone="late" hint={`avg ${formatHours(metrics?.avg_lateness_hours)} · max ${formatHours(metrics?.max_lateness_hours)}`} loading={schedule.isPending} />
        <KpiCard label="At risk" value={metrics?.orders_at_risk ?? "—"} tone="at-risk" loading={schedule.isPending} />
        <KpiCard label="Utilisation" value={formatPct(metrics?.overall_utilization_pct, 0)} tone="running" hint={`makespan ${formatHours(metrics?.makespan_hours)}`} loading={schedule.isPending} />
        <KpiCard label="Setup hours" value={formatHours(metrics?.total_setup_hours)} tone="neutral" hint={`${metrics?.setup_count ?? "—"} changeovers`} loading={schedule.isPending} />
        <KpiCard label="Unscheduled" value={metrics?.unscheduled_orders ?? "—"} tone={metrics && metrics.unscheduled_orders > 0 ? "blocked" : "neutral"} loading={schedule.isPending} />
      </div>

      <Toolbar>
        <ToolbarGroup>
          {(["day", "week", "fortnight"] as GanttZoom[]).map((z) => (
            <button key={z} type="button" className={`btn btn-sm${zoom === z ? " btn-primary" : ""}`} onClick={() => setZoom(z)}>
              {z}
            </button>
          ))}
        </ToolbarGroup>
        <ToolbarGroup>
          <button type="button" className="btn btn-sm" onClick={() => setOffsetDays((d) => d - ZOOM_DAYS[zoom])}>
            ‹ earlier
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setOffsetDays(0)}>
            horizon start
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setOffsetDays((d) => d + ZOOM_DAYS[zoom])}>
            later ›
          </button>
          <span className="text-muted text-xs num">
            {formatDateTime(windowStart, "dd MMM")} – {formatDateTime(windowEnd, "dd MMM")}
          </span>
        </ToolbarGroup>
        <ToolbarGroup>
          <span className="label" style={{ marginBottom: 0 }}>
            Colour
          </span>
          <select className="select" value={colorBy} onChange={(e) => setColorBy(e.target.value as GanttColorMode)}>
            <option value="status">by status</option>
            <option value="customer">by customer</option>
          </select>
        </ToolbarGroup>
        <ToolbarGroup>
          <span className="label" style={{ marginBottom: 0 }}>
            Group
          </span>
          <select className="select" value={group} onChange={(e) => setGroup(e.target.value)}>
            <option value="">All groups</option>
            {groups.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </ToolbarGroup>
        <ToolbarSpacer />
        <span className="text-faint text-xs">
          {schedule.data?.entries.length ?? 0} entries · {rows.length} machines
        </span>
      </Toolbar>

      <AsyncContent query={schedule} isEmpty={(s) => s.entries.length === 0 && rows.length === 0} emptyTitle="No schedule" emptyMessage="Generate a schedule to see machine timelines.">
        {(s) => (
          <GanttChart
            rows={rows}
            entries={s.entries}
            start={windowStart}
            end={windowEnd}
            zoom={zoom}
            now={now}
            colorBy={colorBy}
            selectedEntryId={selected?.entry_id ?? null}
            onEntryClick={setSelected}
            onRowClick={(r) => navigate(routes.machineDetail(encodeURIComponent(r.id)))}
            atRiskSlackHours={config.data?.at_risk_slack_hours ?? 8}
            downtime={downtime}
            rowHeight={zoom === "day" ? 32 : 26}
            maxHeight="calc(100vh - 380px)"
          />
        )}
      </AsyncContent>

      {selected ? (
        <Section
          title={`Entry ${selected.order_id} on ${selected.machine_id}`}
          actions={
            <>
              <button type="button" className="btn btn-sm" onClick={() => navigate(routes.orderDetail(encodeURIComponent(selected.order_id)))}>
                Open order
              </button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSelected(null)}>
                Close
              </button>
            </>
          }
        >
          <dl className="kv">
            <dt>Sequence</dt>
            <dd className="num">#{selected.sequence_on_machine}</dd>
            <dt>Setup</dt>
            <dd>
              {formatDateTime(selected.setup_start)} · {formatMinutes(selected.setup_minutes)} {selected.setup_family ? `(family ${selected.setup_family})` : ""}
            </dd>
            <dt>Run</dt>
            <dd>
              {formatDateTime(selected.start)} → {formatDateTime(selected.end)} · {formatMinutes(selected.run_minutes)} · qty {selected.quantity}
            </dd>
            <dt>Due</dt>
            <dd className={selected.expected_lateness_hours && selected.expected_lateness_hours > 0 ? "tone-late" : ""}>
              {formatDateTime(selected.due_date)}
              {selected.expected_lateness_hours && selected.expected_lateness_hours > 0 ? ` · late by ${formatHours(selected.expected_lateness_hours)}` : ""}
            </dd>
            <dt>Priority</dt>
            <dd className="num">{formatScore(selected.priority_score)}</dd>
            <dt>Locked</dt>
            <dd>{selected.locked ? "yes" : "no"}</dd>
            <dt>Placement reason</dt>
            <dd>{selected.placement_reason}</dd>
          </dl>
        </Section>
      ) : null}
    </div>
  );
}
