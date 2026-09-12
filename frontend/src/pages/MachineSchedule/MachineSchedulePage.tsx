import { addDays } from "date-fns";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useMachineSchedule, useMachines } from "@/api/machines";
import type { MachineListItem, ScheduleEntry } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { FilterBar } from "@/components/FilterBar";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { MachineStatusPill } from "@/components/StatusPill";
import { Timeline } from "@/components/Timeline";
import { humanize } from "@/lib/constants";
import { formatHours, formatMinutes, formatPct, formatScore } from "@/lib/formatters";
import { formatDateTime, toDateKey } from "@/lib/time";
import { useSearchState } from "@/lib/useSearchState";

const FILTER_KEYS = ["group", "status", "process"] as const;

const entryColumns: Column<ScheduleEntry>[] = [
  { key: "seq", header: "#", width: 40, numeric: true, cell: (e) => e.sequence_on_machine, sortValue: (e) => e.sequence_on_machine },
  { key: "order", header: "Order", cell: (e) => <span className="mono strong">{e.order_id}</span>, sortValue: (e) => e.order_id, filterValue: (e) => e.order_id },
  { key: "setup", header: "Setup", cell: (e) => `${formatDateTime(e.setup_start, "dd MMM HH:mm")} · ${formatMinutes(e.setup_minutes)}`, sortValue: (e) => e.setup_start },
  { key: "start", header: "Start", cell: (e) => <span className="num">{formatDateTime(e.start, "dd MMM HH:mm")}</span>, sortValue: (e) => e.start },
  { key: "end", header: "End", cell: (e) => <span className="num">{formatDateTime(e.end, "dd MMM HH:mm")}</span>, sortValue: (e) => e.end },
  { key: "run", header: "Run", numeric: true, cell: (e) => formatMinutes(e.run_minutes), sortValue: (e) => e.run_minutes },
  { key: "qty", header: "Qty", numeric: true, cell: (e) => e.quantity, sortValue: (e) => e.quantity },
  { key: "prio", header: "Prio", numeric: true, cell: (e) => formatScore(e.priority_score), sortValue: (e) => e.priority_score },
  {
    key: "due",
    header: "Due / lateness",
    cell: (e) => (
      <span className={`num ${e.expected_lateness_hours && e.expected_lateness_hours > 0 ? "tone-late" : ""}`}>
        {formatDateTime(e.due_date, "dd MMM HH:mm")}
        {e.expected_lateness_hours && e.expected_lateness_hours > 0 ? ` +${formatHours(e.expected_lateness_hours)}` : ""}
      </span>
    ),
    sortValue: (e) => e.due_date ?? "9999",
  },
  { key: "lock", header: "Lock", width: 50, cell: (e) => (e.locked ? "🔒" : ""), sortValue: (e) => (e.locked ? 1 : 0) },
  { key: "why", header: "Placement", cell: (e) => <span className="truncate text-muted" style={{ maxWidth: 260, display: "inline-block" }} title={e.placement_reason}>{e.placement_reason}</span> },
];

/** Machine list plus per-machine sequence (day timeline + ordered table). */
export default function MachineSchedulePage() {
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const [selected, setSelected] = useState<string | null>(null);
  const [day, setDay] = useState<Date>(() => new Date());
  const machines = useMachines();
  const scheduleParams = useMemo(() => ({ from: toDateKey(addDays(day, -1)), to: toDateKey(addDays(day, 2)) }), [day]);
  const machineSchedule = useMachineSchedule(selected ?? undefined, scheduleParams);

  const groups = useMemo(() => Array.from(new Set((machines.data ?? []).map((m) => m.machine_group))).sort(), [machines.data]);
  const processes = useMemo(() => Array.from(new Set((machines.data ?? []).map((m) => m.process_type))).sort(), [machines.data]);

  const filtered = useMemo(
    () =>
      (machines.data ?? []).filter(
        (m) => (!values.group || m.machine_group === values.group) && (!values.status || m.status === values.status) && (!values.process || m.process_type === values.process),
      ),
    [machines.data, values],
  );

  const machineColumns = useMemo<Column<MachineListItem>[]>(
    () => [
      { key: "id", header: "Machine", cell: (m) => <span className="mono strong">{m.machine_id}</span>, sortValue: (m) => m.machine_id, filterValue: (m) => `${m.machine_id} ${m.machine_name}` },
      { key: "name", header: "Name", cell: (m) => m.machine_name, sortValue: (m) => m.machine_name },
      { key: "group", header: "Group", cell: (m) => m.machine_group, sortValue: (m) => m.machine_group, filterValue: (m) => m.machine_group },
      { key: "process", header: "Process", cell: (m) => humanize(m.process_type), sortValue: (m) => m.process_type },
      { key: "status", header: "Status", cell: (m) => <MachineStatusPill status={m.status} size="sm" />, sortValue: (m) => m.status },
      { key: "queue", header: "Queue", numeric: true, cell: (m) => m.queue_length ?? "—", sortValue: (m) => m.queue_length ?? -1 },
      { key: "hours", header: "Sched h", numeric: true, cell: (m) => formatHours(m.scheduled_hours), sortValue: (m) => m.scheduled_hours ?? -1 },
      { key: "util", header: "Util", numeric: true, cell: (m) => formatPct(m.utilization_pct ?? (m.utilization !== null ? m.utilization * 100 : null), 0), sortValue: (m) => m.utilization_pct ?? -1 },
      { key: "late", header: "Late", numeric: true, cell: (m) => m.late_orders ?? "—", sortValue: (m) => m.late_orders ?? -1 },
      { key: "free", header: "Next free", cell: (m) => <span className="num">{formatDateTime(m.next_free ?? m.available_from, "dd MMM HH:mm")}</span>, sortValue: (m) => m.next_free ?? "" },
    ],
    [],
  );

  return (
    <div className="page">
      <PageHeader eyebrow="Plan" title="Machine Schedule" subtitle="Per-machine sequence: what each machine runs next and why it was placed there." />
      <FilterBar
        fields={[
          { key: "group", label: "Group", kind: "select", options: groups.map((g) => ({ value: g, label: g })) },
          { key: "status", label: "Status", kind: "select", options: ["available", "running", "down", "maintenance", "offline"].map((s) => ({ value: s, label: humanize(s) })) },
          { key: "process", label: "Process", kind: "select", options: processes.map((p) => ({ value: p, label: humanize(p) })) },
        ]}
        values={values}
        onChange={set}
        onReset={reset}
      />
      <Section title="Machines" count={filtered.length} flush>
        <AsyncContent query={machines} emptyTitle="No machines" emptyMessage="Sync the ERP to load master data.">
          {() => (
            <DataTable
              rows={filtered}
              columns={machineColumns}
              rowKey={(m) => m.machine_id}
              selectedKey={selected}
              onRowClick={(m) => setSelected(m.machine_id)}
              rowClassName={(m) => (m.status === "down" ? "row-late" : m.status === "maintenance" ? "row-at-risk" : undefined)}
              initialSort={{ key: "group", direction: "asc" }}
              pageSize={100}
              dense
              filters
            />
          )}
        </AsyncContent>
      </Section>

      <Section
        title={selected ? `Sequence on ${selected}` : "Sequence"}
        actions={
          selected ? (
            <>
              <button type="button" className="btn btn-sm" onClick={() => setDay((d) => addDays(d, -1))}>
                ‹
              </button>
              <span className="num text-sm">{formatDateTime(day, "EEE dd MMM")}</span>
              <button type="button" className="btn btn-sm" onClick={() => setDay((d) => addDays(d, 1))}>
                ›
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setDay(new Date())}>
                today
              </button>
              <button type="button" className="btn btn-sm" onClick={() => navigate(routes.machineDetail(encodeURIComponent(selected)))}>
                Machine detail
              </button>
            </>
          ) : null
        }
        flush
      >
        {!selected ? (
          <EmptyState compact title="Select a machine" message="Click a machine row to see its day timeline and queue." />
        ) : (
          <AsyncContent query={machineSchedule} emptyTitle="Nothing scheduled" emptyMessage="This machine has no entries in the current schedule." compact>
            {(entries) => (
              <div className="col">
                <div style={{ padding: "var(--sp-3)" }}>
                  <Timeline entries={entries} day={day} now={now} onEntryClick={(e) => navigate(routes.orderDetail(encodeURIComponent(e.order_id)))} />
                </div>
                <DataTable
                  rows={entries}
                  columns={entryColumns}
                  rowKey={(e) => e.entry_id}
                  onRowClick={(e) => navigate(routes.orderDetail(encodeURIComponent(e.order_id)))}
                  rowClassName={(e) => (e.expected_lateness_hours && e.expected_lateness_hours > 0 ? "row-late" : e.locked ? "row-hold" : undefined)}
                  initialSort={{ key: "start", direction: "asc" }}
                  pageSize={100}
                  dense
                />
              </div>
            )}
          </AsyncContent>
        )}
      </Section>
    </div>
  );
}
