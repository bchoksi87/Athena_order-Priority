/**
 * Machine Detail: master data, calendar, capability, downtime, locks and a day/week timeline of the
 * machine's schedule entries (GET /machines/{id}/schedule) with lateness colouring.
 */
import { addDays, startOfDay, startOfWeek } from "date-fns";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useMachine, useMachineScheduleView } from "@/api/machines";
import type { DowntimeWindow, ScheduleEntry, ScheduleLock, TimeWindow } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { ErrorState } from "@/components/ErrorState";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { MachineStatusPill, StatusPill } from "@/components/StatusPill";
import { Timeline } from "@/components/Timeline";
import { LOCK_TYPE_LABELS, humanize } from "@/lib/constants";
import { formatHours, formatMinutes, formatNumber, formatPct, formatScore } from "@/lib/formatters";
import { formatDateTime, toUtcIso } from "@/lib/time";

type View = "day" | "week";

const downtimeColumns: Column<DowntimeWindow>[] = [
  { key: "kind", header: "Kind", cell: (w) => <StatusPill tone={w.kind === "unplanned" ? "late" : "at-risk"} size="sm">{humanize(w.kind)}</StatusPill>, sortValue: (w) => w.kind },
  { key: "start", header: "From", cell: (w) => <span className="num">{formatDateTime(w.start)}</span>, sortValue: (w) => w.start },
  { key: "end", header: "To", cell: (w) => <span className="num">{formatDateTime(w.end)}</span>, sortValue: (w) => w.end },
  { key: "dur", header: "Duration", numeric: true, cell: (w) => formatHours((new Date(w.end).getTime() - new Date(w.start).getTime()) / 3_600_000) },
  { key: "reason", header: "Reason", cell: (w) => w.reason || "—" },
];

const lockColumns: Column<ScheduleLock>[] = [
  { key: "type", header: "Lock", cell: (l) => <StatusPill tone="hold" size="sm">{LOCK_TYPE_LABELS[l.lock_type]}</StatusPill> },
  { key: "order", header: "Order", cell: (l) => (l.order_id ? <Link to={routes.orderDetail(encodeURIComponent(l.order_id))} className="mono" onClick={(e) => e.stopPropagation()}>{l.order_id}</Link> : "—") },
  { key: "window", header: "Window", cell: (l) => <span className="num">{l.window ? `${formatDateTime(l.window.start, "dd MMM HH:mm")} → ${formatDateTime(l.window.end, "dd MMM HH:mm")}` : "—"}</span> },
  { key: "by", header: "By", cell: (l) => l.created_by },
  { key: "reason", header: "Reason", cell: (l) => l.reason },
];

/** Exception highlighting: late entries first, then locked ones; finished entries stay neutral. */
function entryRowClass(e: ScheduleEntry, now: Date): string | undefined {
  if (e.expected_lateness_hours !== null && e.expected_lateness_hours > 0) return "row-late";
  if (e.locked) return "row-hold";
  if (new Date(e.end) < now) return "row-done";
  return undefined;
}

export default function MachineDetailPage() {
  const { machineId } = useParams<{ machineId: string }>();
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const [view, setView] = useState<View>("day");
  const [anchor, setAnchor] = useState<Date>(() => startOfDay(new Date()));
  const machine = useMachine(machineId);

  const windowStart = useMemo(() => (view === "week" ? startOfWeek(anchor, { weekStartsOn: 1 }) : startOfDay(anchor)), [view, anchor]);
  const days = view === "week" ? 7 : 1;
  const windowEnd = useMemo(() => addDays(windowStart, days), [windowStart, days]);
  const scheduleParams = useMemo(() => ({ start: toUtcIso(windowStart), end: toUtcIso(windowEnd) }), [windowStart, windowEnd]);
  const schedule = useMachineScheduleView(machineId, scheduleParams);
  const entries = useMemo(() => schedule.data?.entries ?? [], [schedule.data]);
  const [selectedEntry, setSelectedEntry] = useState<string | null>(null);

  const entryColumns = useMemo<Column<ScheduleEntry>[]>(
    () => [
      { key: "seq", header: "#", width: 44, numeric: true, cell: (e) => e.sequence_on_machine, sortValue: (e) => e.sequence_on_machine },
      { key: "order", header: "Order", cell: (e) => <span className="mono strong">{e.order_id}{e.locked ? <span className="pill pill-hold pill-sm" style={{ marginLeft: 6 }}>LOCKED</span> : null}</span>, sortValue: (e) => e.order_id, filterValue: (e) => e.order_id },
      { key: "customer", header: "Customer", cell: (e) => <span className="mono text-muted">{e.customer_id ?? "—"}</span>, filterValue: (e) => e.customer_id ?? "" },
      { key: "setup_start", header: "Setup", cell: (e) => <span className="num">{formatDateTime(e.setup_start, "EEE dd HH:mm")}</span>, sortValue: (e) => e.setup_start },
      { key: "start", header: "Start", cell: (e) => <span className="num">{formatDateTime(e.start, "EEE dd HH:mm")}</span>, sortValue: (e) => e.start },
      { key: "end", header: "End", cell: (e) => <span className="num">{formatDateTime(e.end, "EEE dd HH:mm")}</span>, sortValue: (e) => e.end },
      { key: "setup", header: "Setup", numeric: true, cell: (e) => formatMinutes(e.setup_minutes), sortValue: (e) => e.setup_minutes },
      { key: "run", header: "Run", numeric: true, cell: (e) => formatMinutes(e.run_minutes), sortValue: (e) => e.run_minutes },
      { key: "qty", header: "Qty", numeric: true, cell: (e) => formatNumber(e.quantity), sortValue: (e) => e.quantity },
      { key: "prio", header: "Prio", numeric: true, cell: (e) => formatScore(e.priority_score), sortValue: (e) => e.priority_score },
      { key: "due", header: "Due", cell: (e) => <span className="num">{formatDateTime(e.due_date, "dd MMM HH:mm")}</span>, sortValue: (e) => e.due_date ?? "" },
      { key: "late", header: "Lateness", numeric: true, cell: (e) => (e.expected_lateness_hours !== null && e.expected_lateness_hours > 0 ? <span className="tone-late strong">+{formatHours(e.expected_lateness_hours)}</span> : <span className="tone-ready">on time</span>), sortValue: (e) => e.expected_lateness_hours ?? -1 },
      { key: "family", header: "Setup family / material", cell: (e) => <span className="mono text-muted">{e.setup_family ?? "—"} / {e.material_id ?? "—"}</span> },
      { key: "why", header: "Placement", cell: (e) => <span className="truncate text-muted" style={{ maxWidth: 320, display: "inline-block" }} title={e.placement_reason}>{e.placement_reason}</span> },
    ],
    [],
  );

  return (
    <div className="page" data-testid="machine-detail-page">
      <AsyncContent query={machine} loadingLabel="Loading machine">
        {(d) => {
          const m = d.machine;
          const utilisation = d.load.utilization_pct ?? (m.utilization !== null ? m.utilization * 100 : null);
          const timelineDowntime: TimeWindow[] = [...d.downtime.map((w) => ({ start: w.start, end: w.end, reason: w.reason })), ...(schedule.data?.downtime ?? [])];
          const cal = d.calendar;
          const shifts = Array.isArray(cal.shifts) ? cal.shifts : [];
          const lateInWindow = entries.filter((e) => e.expected_lateness_hours !== null && e.expected_lateness_hours > 0).length;
          const hoursInWindow = entries.reduce((s, e) => s + e.setup_minutes + e.run_minutes, 0) / 60;
          return (
            <>
              <PageHeader
                eyebrow={`Machine · ${m.machine_group} · ${humanize(m.process_type)}`}
                title={`${m.machine_id} — ${m.machine_name}`}
                subtitle={
                  <span className="row row-wrap">
                    <MachineStatusPill status={m.status} />
                    <span>{m.machine_type}</span>
                    {m.location ? <span>· {m.location}</span> : null}
                    <span className="text-faint">· calendar {m.calendar_id ?? "plant default"} ({d.calendar_source})</span>
                    {d.locks.length > 0 ? <span className="pill pill-hold pill-sm">{d.locks.length} LOCK{d.locks.length > 1 ? "S" : ""}</span> : null}
                  </span>
                }
                actions={
                  <>
                    <Link className="btn" to={`${routes.machineSchedule}?machine_id=${encodeURIComponent(m.machine_id)}`}>
                      Machine schedule
                    </Link>
                    <Link className="btn" to={`${routes.gantt}?machine_id=${encodeURIComponent(m.machine_id)}`}>
                      Gantt
                    </Link>
                    <button type="button" className="btn" onClick={() => navigate(-1)}>
                      Back
                    </button>
                  </>
                }
              />
              <div className="grid grid-kpi">
                <KpiCard label="Utilisation" value={formatPct(utilisation, 0)} tone={utilisation !== null && utilisation >= 90 ? "late" : utilisation !== null && utilisation >= 75 ? "at-risk" : "running"} hint={d.load.version_number !== null ? `schedule v${d.load.version_number} (${d.load.status})` : m.utilization !== null ? "ERP trailing utilisation" : "no schedule"} />
                <KpiCard label="Efficiency" value={formatNumber(m.efficiency, 2)} unit="×" tone="neutral" hint={m.capacity_hours_per_day !== null ? `${formatHours(m.capacity_hours_per_day)} capacity / day` : undefined} />
                <KpiCard label="Scheduled" value={formatHours(d.load.scheduled_hours)} tone="neutral" hint={`${d.load.scheduled_entries} entries · setup ${formatHours(d.load.setup_hours)}`} />
                <KpiCard label="Next free" value={formatDateTime(d.load.next_free, "dd MMM HH:mm")} tone={d.load.next_free && new Date(d.load.next_free) <= now ? "ready" : "neutral"} hint={m.available_from ? `available from ${formatDateTime(m.available_from, "dd MMM HH:mm")}` : undefined} />
                <KpiCard label="Downtime windows" value={d.downtime.length} tone={d.downtime.length > 0 ? "at-risk" : "ready"} />
                <KpiCard label="Active locks" value={d.locks.length} tone={d.locks.length > 0 ? "hold" : "neutral"} />
                <KpiCard label="Late in view" value={lateInWindow} tone={lateInWindow > 0 ? "late" : "ready"} hint={`${formatHours(hoursInWindow)} of work in view`} />
              </div>

              <Section
                title={view === "week" ? "Week timeline" : "Day timeline"}
                count={schedule.data ? `${entries.length} entries · v${schedule.data.version_number ?? "—"}` : undefined}
                actions={
                  <>
                    <div className="row gap-1" role="group" aria-label="Timeline view">
                      <button type="button" className={`btn btn-sm${view === "day" ? " btn-primary" : ""}`} onClick={() => setView("day")}>
                        Day
                      </button>
                      <button type="button" className={`btn btn-sm${view === "week" ? " btn-primary" : ""}`} onClick={() => setView("week")}>
                        Week
                      </button>
                    </div>
                    <button type="button" className="btn btn-sm" onClick={() => setAnchor((x) => addDays(x, -days))} aria-label="Previous">
                      ‹
                    </button>
                    <span className="num text-sm">
                      {formatDateTime(windowStart, "EEE dd MMM")}
                      {days > 1 ? ` – ${formatDateTime(addDays(windowEnd, -1), "EEE dd MMM")}` : ""}
                    </span>
                    <button type="button" className="btn btn-sm" onClick={() => setAnchor((x) => addDays(x, days))} aria-label="Next">
                      ›
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => setAnchor(startOfDay(new Date()))}>
                      today
                    </button>
                  </>
                }
              >
                {schedule.isPending ? (
                  <LoadingState compact label="Loading schedule" />
                ) : schedule.isError ? (
                  <ErrorState compact error={schedule.error} onRetry={() => void schedule.refetch()} />
                ) : (
                  <>
                    <Timeline entries={entries} day={windowStart} days={days} now={now} downtime={timelineDowntime} selectedEntryId={selectedEntry} pxPerHour={view === "week" ? 10 : 48} onEntryClick={(e) => setSelectedEntry(e.entry_id)} />
                    <div className="row row-wrap gap-3 text-xs text-muted mt-2">
                      <span><span className="legend-swatch" style={{ background: "var(--status-running)" }} /> on time</span>
                      <span><span className="legend-swatch" style={{ background: "var(--status-at-risk)" }} /> at risk</span>
                      <span><span className="legend-swatch" style={{ background: "var(--status-late)" }} /> late</span>
                      <span><span className="legend-swatch" style={{ background: "var(--status-hold)" }} /> locked</span>
                      <span><span className="legend-swatch" style={{ background: "var(--status-done)" }} /> done</span>
                      <span><span className="legend-swatch" style={{ background: "var(--gantt-setup)" }} /> setup</span>
                      {schedule.data.version_number === null ? <span className="tone-at-risk">no schedule version — generate a schedule to populate this machine</span> : null}
                    </div>
                  </>
                )}
              </Section>

              <div className="grid grid-main-side">
                <div className="col gap-3">
                  <Section title="Sequence in view" count={entries.length} flush>
                    <DataTable
                      rows={entries}
                      columns={entryColumns}
                      rowKey={(e) => e.entry_id}
                      selectedKey={selectedEntry}
                      onRowClick={(e) => navigate(routes.orderDetail(encodeURIComponent(e.order_id)))}
                      rowClassName={(e) => entryRowClass(e, now)}
                      initialSort={{ key: "start", direction: "asc" }}
                      pageSize={200}
                      filters
                      dense
                      emptyMessage="Nothing scheduled on this machine in the selected window"
                    />
                  </Section>
                  <Section title="Upcoming (next 24 h)" count={d.upcoming.length} flush>
                    <DataTable rows={d.upcoming} columns={entryColumns} rowKey={(e) => e.entry_id} onRowClick={(e) => navigate(routes.orderDetail(encodeURIComponent(e.order_id)))} rowClassName={(e) => entryRowClass(e, now)} initialSort={{ key: "start", direction: "asc" }} pageSize={50} dense emptyMessage="No entries in the next 24 hours" />
                  </Section>
                </div>
                <div className="col gap-3">
                  <Section title="Calendar" count={d.calendar_source}>
                    {typeof cal.summary === "string" ? <p className="text-sm text-muted">{cal.summary}</p> : null}
                    {shifts.length > 0 ? (
                      <table className="fld-map">
                        <thead>
                          <tr>
                            <th>Shift</th>
                            <th>Hours</th>
                            <th>Weekdays</th>
                          </tr>
                        </thead>
                        <tbody>
                          {shifts.map((s) => (
                            <tr key={s.name}>
                              <td>{s.name}</td>
                              <td className="num">
                                {s.start}–{s.end}
                                {s.crosses_midnight ? " (+1)" : ""}
                              </td>
                              <td className="mono text-muted">{s.weekdays.map((w) => ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"][w] ?? w).join(" ")}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : null}
                    <dl className="kv mt-2">
                      <dt>Timezone</dt>
                      <dd>{typeof cal.timezone === "string" ? cal.timezone : "—"}</dd>
                      <dt>Holidays</dt>
                      <dd className="num">{typeof cal.holidays === "number" ? cal.holidays : "—"}</dd>
                      <dt>Extra days</dt>
                      <dd className="num">{typeof cal.extra_working_days === "number" ? cal.extra_working_days : "—"}</dd>
                      <dt>Overtime windows</dt>
                      <dd className="num">{typeof cal.overtime_windows === "number" ? cal.overtime_windows : "—"}</dd>
                    </dl>
                  </Section>
                  <Section title="Capability">
                    <dl className="kv">
                      <dt>Processes</dt>
                      <dd>{[m.process_type, ...m.compatible_processes.filter((p) => p !== m.process_type)].map(humanize).join(", ")}</dd>
                      <dt>Materials</dt>
                      <dd className="mono text-sm">{m.compatible_materials.length ? m.compatible_materials.join(", ") : "any"}</dd>
                      <dt>Tooling mounted</dt>
                      <dd className="mono">{m.tooling_configuration.length ? m.tooling_configuration.join(", ") : "—"}</dd>
                      <dt>Current setup</dt>
                      <dd>
                        <span className="mono">{m.current_setup_family ?? "—"}</span> / <span className="mono">{m.current_material_id ?? "—"}</span>
                      </dd>
                      <dt>Preferred rank</dt>
                      <dd className="num">{m.preferred_rank}</dd>
                      <dt>Capacity / day</dt>
                      <dd className="num">{m.capacity_hours_per_day !== null ? formatHours(m.capacity_hours_per_day) : "—"}</dd>
                    </dl>
                  </Section>
                  <Section title="Downtime" count={d.downtime.length} flush>
                    <DataTable rows={d.downtime} columns={downtimeColumns} rowKey={(w) => `${w.kind}-${w.start}-${w.end}`} dense pageSize={50} emptyMessage="No downtime windows" initialSort={{ key: "start", direction: "asc" }} />
                  </Section>
                  <Section title="Locks" count={d.locks.length} flush>
                    <DataTable rows={d.locks} columns={lockColumns} rowKey={(l) => l.lock_id} dense pageSize={50} emptyMessage="No active locks on this machine" />
                  </Section>
                </div>
              </div>
            </>
          );
        }}
      </AsyncContent>
    </div>
  );
}
