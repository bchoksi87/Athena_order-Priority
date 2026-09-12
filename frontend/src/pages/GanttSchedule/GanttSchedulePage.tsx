/**
 * Gantt Schedule: the full SVG Gantt of a schedule version — rows per machine grouped by machine group,
 * day / week / fortnight zoom, now-line, tooltips (order, customer, part, setup, run, lateness), filters
 * (group, process, customer, late only, locked only), a version selector and a "compare with version"
 * mode that draws the baseline's moved / removed entries as ghost bars (GET /schedule/gantt + /compare).
 */
import { addDays, startOfDay } from "date-fns";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useSchedulingConfig } from "@/api/config";
import { useGantt, useScheduleComparison, useSchedulePlan } from "@/api/schedule";
import type { GanttBlock, ProcessType, ScheduleEntry, TimeWindow } from "@/api/types";
import { routes } from "@/app/nav";
import { ComparisonTable } from "@/components/ComparisonTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { GanttChart, type GanttColorMode, type GanttEntryMeta, type GanttRow } from "@/components/GanttChart";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { ScheduleStatusPill } from "@/components/StatusPill";
import { Toolbar, ToolbarGroup, ToolbarSpacer } from "@/components/Toolbar";
import { VersionSelect } from "@/components/VersionSelect";
import { humanize, PROCESS_TYPES } from "@/lib/constants";
import { formatHours, formatMinutes, formatNumber, formatPct, formatScore } from "@/lib/formatters";
import { formatWorkHours } from "@/lib/metricPairs";
import { idleWindows } from "@/lib/scheduleGaps";
import { formatDateTime, parseUtc, toDateKey } from "@/lib/time";
import type { GanttZoom } from "@/lib/timeScale";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["zoom", "from", "group", "process", "customer", "late", "locked", "version", "compare", "color"] as const;
const ZOOM_DAYS: Record<GanttZoom, number> = { day: 1, week: 7, fortnight: 14 };

function parseZoom(v: string): GanttZoom {
  return v === "day" || v === "fortnight" ? v : "week";
}

interface Board {
  rows: GanttRow[];
  entries: ScheduleEntry[];
  meta: Record<string, GanttEntryMeta>;
  downtime: Record<string, TimeWindow[]>;
  nonWorking: Record<string, TimeWindow[]>;
  customers: Array<{ id: string; name: string }>;
  blockByEntry: Map<string, GanttBlock>;
}

export default function GanttSchedulePage() {
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const zoom = parseZoom(values.zoom);
  const colorBy: GanttColorMode = values.color === "customer" ? "customer" : "status";
  const version = values.version ? Number(values.version) : undefined;
  const compareVersion = values.compare ? Number(values.compare) : undefined;
  const [selected, setSelected] = useState<ScheduleEntry | null>(null);

  const plan = useSchedulePlan({ page_size: 1 });
  const config = useSchedulingConfig();
  const active = plan.data?.version ?? null;
  const shownVersionNumber = version ?? active?.version_number;

  // Window: from the requested day (default: today, or the horizon start when the plan is in the future).
  const defaultStart = useMemo(() => {
    const horizon = parseUtc(active?.horizon_start);
    return startOfDay(horizon && horizon > now ? horizon : now);
  }, [active, now]);
  const windowStart = useMemo(() => {
    if (!values.from) return defaultStart;
    const d = new Date(`${values.from}T00:00:00`);
    return Number.isNaN(d.getTime()) ? defaultStart : startOfDay(d);
  }, [values.from, defaultStart]);
  const windowEnd = addDays(windowStart, ZOOM_DAYS[zoom]);

  const query = useMemo(
    () => ({
      version,
      start: windowStart.toISOString(),
      end: windowEnd.toISOString(),
      machine_group: values.group || undefined,
      process_type: (values.process || undefined) as ProcessType | undefined,
    }),
    [version, windowStart, windowEnd, values.group, values.process],
  );
  const gantt = useGantt(query, plan.isSuccess || plan.isError);
  const baseline = useGantt({ ...query, version: compareVersion }, compareVersion !== undefined);
  const comparison = useScheduleComparison(compareVersion, shownVersionNumber);

  const board = useMemo<Board>(() => {
    const rows: GanttRow[] = [];
    const entries: ScheduleEntry[] = [];
    const meta: Record<string, GanttEntryMeta> = {};
    const downtime: Record<string, TimeWindow[]> = {};
    const nonWorking: Record<string, TimeWindow[]> = {};
    const customers = new Map<string, string>();
    const blockByEntry = new Map<string, GanttBlock>();
    const lateOnly = values.late === "1";
    const lockedOnly = values.locked === "1";
    const customer = values.customer;
    const filtering = lateOnly || lockedOnly || Boolean(customer);
    for (const r of gantt.data?.rows ?? []) {
      const kept: ScheduleEntry[] = [];
      for (const b of r.blocks) {
        if (b.customer_id) customers.set(b.customer_id, b.customer_name ?? b.customer_id);
        const late = b.late || (b.entry.expected_lateness_hours ?? 0) > 0;
        if (lateOnly && !late) continue;
        if (lockedOnly && !b.locked) continue;
        if (customer && b.customer_id !== customer) continue;
        kept.push(b.entry);
        meta[b.entry.entry_id] = { customer_name: b.customer_name, part_id: b.part_id, part_name: b.part_name, order_status: b.order_status };
        blockByEntry.set(b.entry.entry_id, b);
      }
      if (filtering && kept.length === 0) continue;
      rows.push({ id: r.machine_id, label: r.machine_name || r.machine_id, sublabel: `${r.machine_id} · ${humanize(r.status)} · ${formatHours(r.busy_hours)}`, group: r.machine_group });
      entries.push(...kept);
      downtime[r.machine_id] = r.downtime;
      nonWorking[r.machine_id] = idleWindows(r.blocks.map((b) => b.entry), 30);
    }
    return { rows, entries, meta, downtime, nonWorking, customers: Array.from(customers, ([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name)), blockByEntry };
  }, [gantt.data, values.late, values.locked, values.customer]);

  const baselineEntries = useMemo(() => (compareVersion !== undefined ? (baseline.data?.rows ?? []).flatMap((r) => r.blocks.map((b) => b.entry)) : undefined), [baseline.data, compareVersion]);
  const groups = useMemo(() => Array.from(new Set((gantt.data?.rows ?? []).map((r) => r.machine_group))).sort(), [gantt.data]);
  const shownVersion = gantt.data?.version ?? active;
  const metrics = shownVersion?.metrics;
  const lateInWindow = board.entries.filter((e) => (e.expected_lateness_hours ?? 0) > 0).length;
  const filtersActive = Boolean(values.group || values.process || values.customer || values.late || values.locked);
  const selectedBlock = selected ? board.blockByEntry.get(selected.entry_id) : undefined;

  return (
    <div className="page" data-testid="gantt-schedule">
      <PageHeader
        eyebrow="Plan"
        title="Gantt Schedule"
        subtitle={
          shownVersion ? (
            <span>
              Schedule v{shownVersion.version_number} <ScheduleStatusPill status={shownVersion.status} size="sm" /> · {shownVersion.algorithm} v{shownVersion.algorithm_version} · {shownVersion.profile_id} v{shownVersion.profile_version} · generated{" "}
              {formatDateTime(shownVersion.generated_at)}
              {shownVersion.generated_by ? ` by ${shownVersion.generated_by}` : ""} · horizon {formatDateTime(shownVersion.horizon_start, "dd MMM")} – {formatDateTime(shownVersion.horizon_end, "dd MMM")}
            </span>
          ) : plan.isPending ? (
            "Loading the active plan…"
          ) : (
            "No schedule version yet — generate one from the control tower to populate the chart."
          )
        }
        actions={
          <>
            <VersionSelect value={version ?? null} onChange={(v) => set("version", v === null ? "" : String(v))} active={active} label="Version" />
            <VersionSelect value={compareVersion ?? null} onChange={(v) => set("compare", v === null ? "" : String(v))} activeOptionLabel={null} exclude={shownVersionNumber !== undefined ? [shownVersionNumber] : []} label="Compare with" ariaLabel="Compare with version" />
            {compareVersion !== undefined ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => set("compare", "")}>
                Exit compare
              </button>
            ) : null}
            <button type="button" className="btn btn-sm" onClick={() => navigate(routes.machineSchedule)}>
              Machine board
            </button>
          </>
        }
      />

      <div className="grid grid-kpi">
        <KpiCard label="Quality" value={formatScore(shownVersion?.quality_score)} unit="/100" tone="running" hint={shownVersion?.quality_summary ?? undefined} loading={plan.isPending} />
        <KpiCard label="On time" value={formatPct(metrics?.on_time_pct, 0)} tone={(metrics?.on_time_pct ?? 0) >= 90 ? "ready" : (metrics?.on_time_pct ?? 0) >= 75 ? "at-risk" : "late"} hint={`${formatNumber(metrics?.on_time_orders)} of ${formatNumber(metrics?.scheduled_orders)} orders`} loading={plan.isPending} />
        <KpiCard label="Late" value={formatNumber(metrics?.late_orders)} tone={(metrics?.late_orders ?? 0) > 0 ? "late" : "ready"} hint={`avg ${formatHours(metrics?.avg_lateness_hours)} · max ${formatHours(metrics?.max_lateness_hours)}`} loading={plan.isPending} />
        <KpiCard label="At risk" value={formatNumber(metrics?.orders_at_risk)} tone="at-risk" loading={plan.isPending} />
        <KpiCard label="Utilisation" value={formatPct(metrics?.overall_utilization_pct, 0)} tone="running" hint={`makespan ${formatWorkHours(metrics?.makespan_hours, 0)}`} loading={plan.isPending} />
        <KpiCard label="Setup hours" value={formatWorkHours(metrics?.total_setup_hours, 0)} tone="neutral" hint={`${formatNumber(metrics?.setup_count)} changeovers`} loading={plan.isPending} />
        <KpiCard label="Unscheduled" value={formatNumber(metrics?.unscheduled_orders)} tone={(metrics?.unscheduled_orders ?? 0) > 0 ? "blocked" : "neutral"} loading={plan.isPending} />
        <KpiCard label="In window" value={formatNumber(board.entries.length)} tone={lateInWindow > 0 ? "late" : "neutral"} hint={`${formatNumber(lateInWindow)} late · ${formatNumber(board.rows.length)} machines`} loading={gantt.isPending} />
      </div>

      <Toolbar>
        <ToolbarGroup>
          {(["day", "week", "fortnight"] as GanttZoom[]).map((z) => (
            <button key={z} type="button" className={`btn btn-sm${zoom === z ? " btn-primary" : ""}`} onClick={() => set("zoom", z === "week" ? "" : z)} aria-pressed={zoom === z}>
              {z}
            </button>
          ))}
        </ToolbarGroup>
        <ToolbarGroup>
          <button type="button" className="btn btn-sm" onClick={() => set("from", toDateKey(addDays(windowStart, -ZOOM_DAYS[zoom])))} aria-label="Earlier">
            ‹ earlier
          </button>
          <input className="input" type="date" value={toDateKey(windowStart)} onChange={(e) => set("from", e.target.value)} aria-label="Window start" style={{ width: 150 }} />
          <button type="button" className="btn btn-sm" onClick={() => set("from", toDateKey(addDays(windowStart, ZOOM_DAYS[zoom])))} aria-label="Later">
            later ›
          </button>
          <button type="button" className="btn btn-sm" onClick={() => set("from", "")}>
            today
          </button>
          {active ? (
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => set("from", toDateKey(parseUtc(active.horizon_start) ?? now))}>
              horizon start
            </button>
          ) : null}
          <span className="text-muted text-xs num">
            {formatDateTime(windowStart, "dd MMM")} – {formatDateTime(windowEnd, "dd MMM")}
          </span>
        </ToolbarGroup>
        <ToolbarGroup>
          <select className="select" value={values.group} onChange={(e) => set("group", e.target.value)} aria-label="Machine group">
            <option value="">All groups</option>
            {groups.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
          <select className="select" value={values.process} onChange={(e) => set("process", e.target.value)} aria-label="Process">
            <option value="">All processes</option>
            {PROCESS_TYPES.map((p) => (
              <option key={p} value={p}>
                {humanize(p)}
              </option>
            ))}
          </select>
          <select className="select" value={values.customer} onChange={(e) => set("customer", e.target.value)} aria-label="Customer" style={{ maxWidth: 200 }}>
            <option value="">All customers</option>
            {board.customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <label className="row gap-1 text-sm">
            <input type="checkbox" checked={values.late === "1"} onChange={(e) => set("late", e.target.checked ? "1" : "")} /> late only
          </label>
          <label className="row gap-1 text-sm">
            <input type="checkbox" checked={values.locked === "1"} onChange={(e) => set("locked", e.target.checked ? "1" : "")} /> locked only
          </label>
          <select className="select" value={colorBy} onChange={(e) => set("color", e.target.value === "customer" ? "customer" : "")} aria-label="Colour by">
            <option value="status">colour by status</option>
            <option value="customer">colour by customer</option>
          </select>
          <button type="button" className="btn btn-sm btn-ghost" onClick={reset} disabled={!filtersActive && !values.version && !values.compare && !values.zoom && !values.from}>
            Reset
          </button>
        </ToolbarGroup>
        <ToolbarSpacer />
        <span className="text-faint text-xs">
          {gantt.data ? `${formatNumber(gantt.data.entries)} entries in window · ${formatNumber(gantt.data.rows.length)} machines` : ""}
        </span>
      </Toolbar>

      {compareVersion !== undefined ? (
        <Section title={`Compare v${compareVersion} → v${shownVersionNumber ?? "?"}`} count={comparison.data ? `${formatNumber(comparison.data.moved_orders)} orders moved · ${formatNumber(comparison.data.changes.moved_entries)} moved / ${formatNumber(comparison.data.changes.added_entries)} added / ${formatNumber(comparison.data.changes.removed_entries)} removed entries` : undefined}>
          {comparison.isPending && compareVersion !== shownVersionNumber ? (
            <LoadingState compact label="Comparing versions" />
          ) : comparison.isError ? (
            <ErrorState compact error={comparison.error} onRetry={() => void comparison.refetch()} />
          ) : comparison.data ? (
            <div className="col gap-2">
              <ComparisonTable metrics={comparison.data.metrics} quality={comparison.data.quality} beforeLabel={`v${comparison.data.a.version_number}`} afterLabel={`v${comparison.data.b.version_number}`} dense />
              <div className="text-xs text-faint">{comparison.data.summary}</div>
              {comparison.data.changes.frozen_violations > 0 ? <div className="text-xs tone-late">{comparison.data.changes.frozen_violations} change(s) inside the frozen window.</div> : null}
            </div>
          ) : (
            <span className="text-muted text-sm">Pick two different versions to compare.</span>
          )}
        </Section>
      ) : null}

      {gantt.isPending && !gantt.data ? (
        <LoadingState label="Loading Gantt window" />
      ) : gantt.isError ? (
        <ErrorState error={gantt.error} onRetry={() => void gantt.refetch()} />
      ) : board.rows.length === 0 ? (
        <EmptyState title={filtersActive ? "Nothing matches the filters" : "No entries in this window"} message={filtersActive ? "Loosen the filters or move the window." : "Move the window (earlier / later) or generate a schedule."} />
      ) : (
        <GanttChart
          rows={board.rows}
          entries={board.entries}
          start={windowStart}
          end={windowEnd}
          zoom={zoom}
          now={now}
          colorBy={colorBy}
          selectedEntryId={selected?.entry_id ?? null}
          onEntryClick={setSelected}
          onClusterClick={(entries) => {
            const first = entries[0];
            if (first) {
              set("zoom", "day");
              set("from", toDateKey(parseUtc(first.start) ?? windowStart));
            }
          }}
          onRowClick={(r) => navigate(routes.machineDetail(encodeURIComponent(r.id)))}
          atRiskSlackHours={config.data?.at_risk_slack_hours ?? 8}
          downtime={board.downtime}
          nonWorking={board.nonWorking}
          meta={board.meta}
          baselineEntries={baselineEntries}
          baselineLabel={compareVersion !== undefined ? `v${compareVersion}` : undefined}
          rowHeight={zoom === "day" ? 32 : 26}
          labelWidth={200}
          maxHeight="calc(100vh - 400px)"
        />
      )}

      {selected ? (
        <Section
          title={`${selected.order_id} on ${selected.machine_id}`}
          actions={
            <>
              <button type="button" className="btn btn-sm btn-primary" onClick={() => navigate(routes.orderDetail(encodeURIComponent(selected.order_id)))}>
                Open order
              </button>
              <button type="button" className="btn btn-sm" onClick={() => navigate(routes.machineDetail(encodeURIComponent(selected.machine_id)))}>
                Open machine
              </button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSelected(null)}>
                Close
              </button>
            </>
          }
        >
          <dl className="kv">
            {selectedBlock?.customer_name || selected.customer_id ? (
              <>
                <dt>Customer</dt>
                <dd>{selectedBlock?.customer_name ?? selected.customer_id}</dd>
              </>
            ) : null}
            {selectedBlock?.part_name || selectedBlock?.part_id ? (
              <>
                <dt>Part</dt>
                <dd>{selectedBlock.part_name ?? selectedBlock.part_id}</dd>
              </>
            ) : null}
            <dt>Sequence</dt>
            <dd className="num">#{selected.sequence_on_machine} · op {selected.operation_id}</dd>
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
              {selected.expected_lateness_hours && selected.expected_lateness_hours > 0 ? ` · late by ${formatHours(selected.expected_lateness_hours)}` : selected.due_date ? " · on time" : ""}
            </dd>
            <dt>Priority</dt>
            <dd className="num">{formatScore(selected.priority_score)}</dd>
            <dt>Locked</dt>
            <dd>{selected.locked ? "yes — kept across replans" : "no"}</dd>
            <dt>Placement reason</dt>
            <dd>{selected.placement_reason}</dd>
          </dl>
        </Section>
      ) : null}
    </div>
  );
}
