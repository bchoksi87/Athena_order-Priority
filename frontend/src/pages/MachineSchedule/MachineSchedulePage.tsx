/**
 * Machine Schedule (spec Phase 8 MACHINE VIEW): the machine-centric board. Day view (per-machine
 * timeline from GET /schedule/{date}, setup hatched, downtime hatched, idle gaps shaded), week view
 * (GET /schedule/gantt), and the list view in the spec's "08:00 — Setup — Job 1045" format; machine
 * group / process / machine-set filters, lateness colouring, locks, version selector.
 */
import { addDays, startOfDay } from "date-fns";
import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useSchedulingConfig } from "@/api/config";
import { useDaySchedule, useGantt, useSchedulePlan } from "@/api/schedule";
import type { GanttBlock, MachineRow, ProcessType, ScheduleEntry, TimeWindow } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { GanttChart, type GanttEntryMeta, type GanttRow } from "@/components/GanttChart";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { MachineStatusPill, ScheduleStatusPill } from "@/components/StatusPill";
import { Toolbar, ToolbarGroup, ToolbarSpacer } from "@/components/Toolbar";
import { VersionSelect } from "@/components/VersionSelect";
import { humanize, PROCESS_TYPES } from "@/lib/constants";
import { entryTone } from "@/lib/entryTone";
import { formatHours, formatNumber } from "@/lib/formatters";
import { formatWorkHours } from "@/lib/metricPairs";
import { idleWindows, machineLines, type MachineLine } from "@/lib/scheduleGaps";
import { formatDateTime, parseUtc, toDateKey } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

import "./MachineSchedule.css";

const FILTER_KEYS = ["view", "date", "group", "process", "machines", "version"] as const;
type View = "day" | "week" | "list";
const IDLE_MIN_MINUTES = 15;

function parseView(v: string): View {
  return v === "week" || v === "list" ? v : "day";
}

function parseDate(v: string): Date {
  const d = v ? new Date(`${v}T00:00:00`) : new Date();
  return Number.isNaN(d.getTime()) ? startOfDay(new Date()) : startOfDay(d);
}

function lineClass(line: MachineLine, now: Date, slack: number): string {
  const cls = [`ms-line`, `ms-line-${line.kind}`];
  if (line.entry) {
    const tone = entryTone(line.entry, now, slack);
    if (tone === "late") cls.push("ms-line-late");
    else if (tone === "at-risk") cls.push("ms-line-at-risk");
    if (line.entry.locked) cls.push("ms-line-locked");
  }
  return cls.join(" ");
}

function BoardState({ pending, error, onRetry, children }: { pending: boolean; error: unknown; onRetry: () => void; children: React.ReactNode }) {
  if (pending) return <LoadingState label="Loading machine board" />;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  return <>{children}</>;
}

function rowsToGantt(rows: MachineRow[]): { gantt: GanttRow[]; entries: ScheduleEntry[]; meta: Record<string, GanttEntryMeta>; downtime: Record<string, TimeWindow[]>; nonWorking: Record<string, TimeWindow[]>; blocks: Map<string, GanttBlock[]> } {
  const gantt: GanttRow[] = [];
  const entries: ScheduleEntry[] = [];
  const meta: Record<string, GanttEntryMeta> = {};
  const downtime: Record<string, TimeWindow[]> = {};
  const nonWorking: Record<string, TimeWindow[]> = {};
  const blocks = new Map<string, GanttBlock[]>();
  for (const r of rows) {
    gantt.push({ id: r.machine_id, label: r.machine_name || r.machine_id, sublabel: `${r.machine_id} · ${humanize(r.status)} · ${formatHours(r.busy_hours)}`, group: r.machine_group });
    for (const b of r.blocks) {
      entries.push(b.entry);
      meta[b.entry.entry_id] = { customer_name: b.customer_name, part_id: b.part_id, part_name: b.part_name, order_status: b.order_status };
    }
    downtime[r.machine_id] = r.downtime;
    nonWorking[r.machine_id] = idleWindows(r.blocks.map((b) => b.entry), IDLE_MIN_MINUTES);
    blocks.set(r.machine_id, r.blocks);
  }
  return { gantt, entries, meta, downtime, nonWorking, blocks };
}

export default function MachineSchedulePage() {
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const { values, set, setMany, reset } = useSearchState(FILTER_KEYS);
  const view = parseView(values.view);
  const day = useMemo(() => parseDate(values.date), [values.date]);
  const dateKey = toDateKey(day);
  const version = values.version ? Number(values.version) : undefined;
  const machineSet = useMemo(() => values.machines.split(",").filter(Boolean), [values.machines]);
  const [selectedEntry, setSelectedEntry] = useState<string | null>(null);

  const plan = useSchedulePlan({ page_size: 1 });
  const config = useSchedulingConfig();
  const slack = config.data?.at_risk_slack_hours ?? 8;

  const weekStart = day;
  const weekEnd = addDays(day, 7);
  const dayView = useDaySchedule(view === "week" ? undefined : dateKey, version);
  const weekView = useGantt(
    {
      version,
      start: weekStart.toISOString(),
      end: weekEnd.toISOString(),
      machine_group: values.group || undefined,
      process_type: (values.process || undefined) as ProcessType | undefined,
      machine_id: machineSet.length ? machineSet : undefined,
    },
    view === "week",
  );

  // Every machine of the plant for the pickers comes from whichever view is loaded (the day view is unfiltered).
  const allRows = useMemo<MachineRow[]>(() => dayView.data?.machines ?? weekView.data?.rows ?? [], [dayView.data, weekView.data]);
  const groups = useMemo(() => Array.from(new Set(allRows.map((r) => r.machine_group))).sort(), [allRows]);
  const processes = useMemo(() => Array.from(new Set(allRows.map((r) => r.process_type))).sort(), [allRows]);
  const pickable = useMemo(() => allRows.filter((r) => (!values.group || r.machine_group === values.group) && (!values.process || r.process_type === values.process)), [allRows, values.group, values.process]);

  const filterRows = useCallback(
    (rows: MachineRow[]) => rows.filter((r) => (!values.group || r.machine_group === values.group) && (!values.process || r.process_type === values.process) && (machineSet.length === 0 || machineSet.includes(r.machine_id))),
    [values.group, values.process, machineSet],
  );

  const dayRows = useMemo(() => filterRows(dayView.data?.machines ?? []), [dayView.data, filterRows]);
  const weekRows = useMemo(() => filterRows(weekView.data?.rows ?? []), [weekView.data, filterRows]);
  const activeRows = view === "week" ? weekRows : dayRows;
  const board = useMemo(() => rowsToGantt(activeRows), [activeRows]);

  const stats = useMemo(() => {
    const entries = board.entries;
    return {
      machines: activeRows.length,
      entries: entries.length,
      busy: activeRows.reduce((s, r) => s + r.busy_hours, 0),
      late: entries.filter((e) => (e.expected_lateness_hours ?? 0) > 0).length,
      locked: entries.filter((e) => e.locked).length,
      downtime: activeRows.reduce((s, r) => s + r.downtime.length, 0),
      idle: Object.values(board.nonWorking).reduce((s, w) => s + w.length, 0),
    };
  }, [board, activeRows]);

  const versionInfo = view === "week" ? weekView.data?.version : dayView.data?.version;
  const axis = view === "week" ? { start: weekStart, end: weekEnd } : { start: parseUtc(dayView.data?.start) ?? day, end: parseUtc(dayView.data?.end) ?? addDays(day, 1) };
  const step = view === "week" ? 7 : 1;
  const toggleMachine = (id: string) => set("machines", (machineSet.includes(id) ? machineSet.filter((m) => m !== id) : [...machineSet, id]).join(","));
  const query = view === "week" ? weekView : dayView;

  return (
    <div className="page" data-testid="machine-schedule">
      <PageHeader
        eyebrow="Plan"
        title="Machine Schedule"
        subtitle={
          <span>
            Machine-centric board: what each machine runs, when, and why it was placed there.{" "}
            {versionInfo ? (
              <>
                Plan v{versionInfo.version_number} <ScheduleStatusPill status={versionInfo.status} size="sm" /> · generated {formatDateTime(versionInfo.generated_at)}
              </>
            ) : plan.data?.version ? (
              `Active plan v${plan.data.version.version_number}`
            ) : (
              "No schedule yet"
            )}
          </span>
        }
        actions={
          <>
            <VersionSelect value={version ?? null} onChange={(v) => set("version", v === null ? "" : String(v))} active={plan.data?.version} label="Version" />
            <button type="button" className="btn btn-sm" onClick={() => navigate(routes.gantt)}>
              Full Gantt
            </button>
          </>
        }
      />

      <div className="grid grid-kpi">
        <KpiCard label="Machines shown" value={formatNumber(stats.machines)} tone="neutral" loading={query.isPending} hint={values.group || values.process || machineSet.length ? "filtered" : "whole plant"} />
        <KpiCard label="Jobs" value={formatNumber(stats.entries)} tone="running" loading={query.isPending} hint={view === "week" ? "in the 7-day window" : "on this day"} />
        <KpiCard label="Busy hours" value={formatWorkHours(stats.busy, 0)} tone="running" loading={query.isPending} hint="setup + run" />
        <KpiCard label="Late jobs" value={formatNumber(stats.late)} tone={stats.late > 0 ? "late" : "ready"} loading={query.isPending} />
        <KpiCard label="Locked" value={formatNumber(stats.locked)} tone={stats.locked > 0 ? "hold" : "neutral"} loading={query.isPending} hint="kept across replans" />
        <KpiCard label="Downtime windows" value={formatNumber(stats.downtime)} tone={stats.downtime > 0 ? "at-risk" : "ready"} loading={query.isPending} hint={`${stats.idle} idle gaps ≥ ${IDLE_MIN_MINUTES} min`} />
      </div>

      <Toolbar>
        <ToolbarGroup>
          {(["day", "week", "list"] as View[]).map((v) => (
            <button key={v} type="button" className={`btn btn-sm${view === v ? " btn-primary" : ""}`} onClick={() => set("view", v === "day" ? "" : v)} aria-pressed={view === v}>
              {v === "day" ? "Day view" : v === "week" ? "Week view" : "List view"}
            </button>
          ))}
        </ToolbarGroup>
        <ToolbarGroup>
          <button type="button" className="btn btn-sm" onClick={() => set("date", toDateKey(addDays(day, -step)))} aria-label="Previous">
            ‹
          </button>
          <input className="input" type="date" value={dateKey} onChange={(e) => set("date", e.target.value)} aria-label="Date" style={{ width: 150 }} />
          <button type="button" className="btn btn-sm" onClick={() => set("date", toDateKey(addDays(day, step)))} aria-label="Next">
            ›
          </button>
          <button type="button" className="btn btn-sm" onClick={() => set("date", "")}>
            today
          </button>
          <span className="text-muted text-xs num">
            {view === "week" ? `${formatDateTime(weekStart, "EEE dd MMM")} – ${formatDateTime(addDays(weekEnd, -1), "EEE dd MMM")}` : `${formatDateTime(day, "EEE dd MMM yyyy")}${dayView.data ? ` · plant day ${dayView.data.timezone}` : ""}`}
          </span>
        </ToolbarGroup>
        <ToolbarGroup>
          <select className="select" value={values.group} onChange={(e) => setMany({ group: e.target.value, machines: "" })} aria-label="Machine group">
            <option value="">All groups</option>
            {groups.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
          <select className="select" value={values.process} onChange={(e) => setMany({ process: e.target.value, machines: "" })} aria-label="Process">
            <option value="">All processes</option>
            {(processes.length ? processes : PROCESS_TYPES).map((p) => (
              <option key={p} value={p}>
                {humanize(p)}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-sm btn-ghost" onClick={reset} disabled={!values.group && !values.process && !values.machines && !values.version}>
            Reset
          </button>
        </ToolbarGroup>
        <ToolbarSpacer />
        <span className="text-faint text-xs">click a bar for the order · click a machine for its detail</span>
      </Toolbar>

      {pickable.length > 0 ? (
        <div className="ms-chips" role="group" aria-label="Machine set">
          {pickable.map((r) => {
            const on = machineSet.includes(r.machine_id);
            return (
              <button key={r.machine_id} type="button" className={`chip${on ? " active" : ""}`} aria-pressed={on} onClick={() => toggleMachine(r.machine_id)} title={`${r.machine_name} · ${r.machine_group} · ${humanize(r.status)}`}>
                {r.machine_id}
              </button>
            );
          })}
          {machineSet.length ? (
            <button type="button" className="chip" onClick={() => set("machines", "")}>
              clear set ({machineSet.length})
            </button>
          ) : null}
        </div>
      ) : null}

      {view === "list" ? (
        <Section title={`Machine list · ${formatDateTime(day, "EEE dd MMM")}`} count={`${activeRows.length} machines`} flush>
          <AsyncContent query={dayView} isEmpty={(d) => d.machines.length === 0} emptyTitle="No schedule for this day" emptyMessage="Generate a schedule or pick another day.">
            {() =>
              activeRows.length === 0 ? (
                <EmptyState title="No machines match" message="Loosen the group / process / machine-set filters." compact />
              ) : (
                <div className="ms-list section-body" data-testid="machine-list">
                  {activeRows.map((r) => {
                    const lines = machineLines(r.blocks, r.downtime, IDLE_MIN_MINUTES);
                    return (
                      <div key={r.machine_id} className="ms-machine" data-testid="machine-list-item">
                        <div className="ms-machine-head">
                          <button type="button" className="btn btn-sm btn-ghost ms-machine-name" onClick={() => navigate(routes.machineDetail(encodeURIComponent(r.machine_id)))}>
                            Machine {r.machine_id}
                          </button>
                          <span className="text-muted text-sm">{r.machine_name}</span>
                          <span className="text-faint text-xs">{r.machine_group} · {humanize(r.process_type)}</span>
                          <MachineStatusPill status={r.status as "available" | "running" | "down" | "maintenance" | "offline"} size="sm" />
                          <span className="text-muted text-xs num">busy {formatHours(r.busy_hours)} · {r.blocks.length} jobs{r.locks.length ? ` · ${r.locks.length} locks` : ""}</span>
                        </div>
                        {lines.length === 0 ? (
                          <div className="text-faint text-sm" style={{ padding: "var(--sp-2) var(--sp-3)" }}>
                            Nothing scheduled on this day
                          </div>
                        ) : (
                          <ul className="ms-lines">
                            {lines.map((line, i) => (
                              <li
                                key={`${line.kind}-${i}`}
                                className={lineClass(line, now, slack)}
                                onClick={line.entry ? () => navigate(routes.orderDetail(encodeURIComponent(line.entry?.order_id ?? ""))) : undefined}
                                title={line.reason ?? undefined}
                                data-testid="machine-line"
                              >
                                <span className="num">{formatDateTime(line.at, "HH:mm")}</span>
                                <span className="ms-line-dash">—</span>
                                <span className="ms-line-label">
                                  {line.label}
                                  {line.entry?.locked ? " 🔒" : ""}
                                </span>
                                <span className="ms-line-meta">
                                  {line.kind === "job" && line.block ? `${line.block.customer_name ?? line.entry?.customer_id ?? ""}${line.block.part_name ? ` · ${line.block.part_name}` : ""} · qty ${line.entry?.quantity ?? ""}` : ""}
                                  {line.entry && (line.entry.expected_lateness_hours ?? 0) > 0 ? ` · late +${formatHours(line.entry.expected_lateness_hours)}` : ""}
                                  {line.kind === "job" && line.entry && (line.entry.expected_lateness_hours ?? 0) <= 0 && line.entry.due_date ? ` · due ${formatDateTime(line.entry.due_date, "dd MMM HH:mm")}` : ""}
                                  {line.kind !== "job" ? ` → ${formatDateTime(line.end, "HH:mm")}` : ""}
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    );
                  })}
                </div>
              )
            }
          </AsyncContent>
        </Section>
      ) : (
        <BoardState pending={query.isPending} error={query.isError ? query.error : null} onRetry={() => void query.refetch()}>
          {activeRows.length === 0 ? (
              <EmptyState title="No machines match" message="Loosen the group / process / machine-set filters, or pick another version." />
            ) : (
              <GanttChart
                rows={board.gantt}
                entries={board.entries}
                start={axis.start}
                end={axis.end}
                zoom={view === "week" ? "week" : "day"}
                now={now}
                selectedEntryId={selectedEntry}
                onEntryClick={(e) => {
                  setSelectedEntry(e.entry_id);
                  navigate(routes.orderDetail(encodeURIComponent(e.order_id)));
                }}
                onClusterClick={(entries) => {
                  const first = entries[0];
                  if (first) setMany({ view: "", date: toDateKey(parseUtc(first.start) ?? day) });
                }}
                onRowClick={(r) => navigate(routes.machineDetail(encodeURIComponent(r.id)))}
                atRiskSlackHours={slack}
                downtime={board.downtime}
                nonWorking={board.nonWorking}
                meta={board.meta}
                rowHeight={view === "day" ? 32 : 26}
                labelWidth={210}
                maxHeight="calc(100vh - 420px)"
              />
            )}
        </BoardState>
      )}
    </div>
  );
}
