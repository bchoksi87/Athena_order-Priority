import { addDays } from "date-fns";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useMachine, useMachineSchedule } from "@/api/machines";
import type { ScheduleEntry, TimeWindow } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { MachineStatusPill } from "@/components/StatusPill";
import { Timeline } from "@/components/Timeline";
import { humanize } from "@/lib/constants";
import { formatHours, formatMinutes, formatNumber, formatPct, formatScore } from "@/lib/formatters";
import { formatDateTime, toDateKey } from "@/lib/time";

import { customerColumn, dueColumn, orderIdColumn, orderRowClass, partColumn, readinessColumn, scoreColumn } from "../shared/orderColumns";

const downtimeColumns: Column<TimeWindow>[] = [
  { key: "start", header: "From", cell: (w) => <span className="num">{formatDateTime(w.start)}</span>, sortValue: (w) => w.start },
  { key: "end", header: "To", cell: (w) => <span className="num">{formatDateTime(w.end)}</span>, sortValue: (w) => w.end },
  { key: "reason", header: "Reason", cell: (w) => w.reason || "—" },
];

const entryColumns: Column<ScheduleEntry>[] = [
  { key: "seq", header: "#", width: 40, numeric: true, cell: (e) => e.sequence_on_machine, sortValue: (e) => e.sequence_on_machine },
  { key: "order", header: "Order", cell: (e) => <span className="mono strong">{e.order_id}</span>, sortValue: (e) => e.order_id },
  { key: "start", header: "Start", cell: (e) => <span className="num">{formatDateTime(e.start, "dd MMM HH:mm")}</span>, sortValue: (e) => e.start },
  { key: "end", header: "End", cell: (e) => <span className="num">{formatDateTime(e.end, "dd MMM HH:mm")}</span>, sortValue: (e) => e.end },
  { key: "setup", header: "Setup", numeric: true, cell: (e) => formatMinutes(e.setup_minutes), sortValue: (e) => e.setup_minutes },
  { key: "run", header: "Run", numeric: true, cell: (e) => formatMinutes(e.run_minutes), sortValue: (e) => e.run_minutes },
  { key: "prio", header: "Prio", numeric: true, cell: (e) => formatScore(e.priority_score), sortValue: (e) => e.priority_score },
  { key: "why", header: "Placement", cell: (e) => <span className="truncate text-muted" style={{ maxWidth: 300, display: "inline-block" }} title={e.placement_reason}>{e.placement_reason}</span> },
];

/** Machine master data, capability, downtime, day timeline and queued work. */
export default function MachineDetailPage() {
  const { machineId } = useParams<{ machineId: string }>();
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const [day, setDay] = useState<Date>(() => new Date());
  const machine = useMachine(machineId);
  const scheduleParams = useMemo(() => ({ from: toDateKey(addDays(day, -1)), to: toDateKey(addDays(day, 2)) }), [day]);
  const entries = useMachineSchedule(machineId, scheduleParams);

  return (
    <div className="page">
      <AsyncContent query={machine} loadingLabel="Loading machine">
        {(d) => {
          const m = d.machine;
          const downtime = [...m.maintenance_windows, ...m.planned_downtime, ...m.unplanned_downtime, ...(d.downtime ?? [])];
          const dayEntries = entries.data ?? d.entries;
          return (
            <>
              <PageHeader
                eyebrow={`Machine · ${m.machine_group}`}
                title={`${m.machine_id} — ${m.machine_name}`}
                subtitle={
                  <span className="row">
                    <MachineStatusPill status={m.status} /> {humanize(m.process_type)} · {m.machine_type}
                    {m.location ? ` · ${m.location}` : ""}
                  </span>
                }
                actions={
                  <button type="button" className="btn" onClick={() => navigate(routes.machineSchedule)}>
                    All machines
                  </button>
                }
              />
              <div className="grid grid-kpi">
                <KpiCard label="Utilisation" value={formatPct(d.utilization_pct ?? (m.utilization !== null ? m.utilization * 100 : null), 0)} tone="running" />
                <KpiCard label="Queued orders" value={d.queue.length} tone={d.queue.length > 0 ? "neutral" : "ready"} />
                <KpiCard label="Scheduled entries" value={d.entries.length} tone="neutral" />
                <KpiCard label="Efficiency" value={formatNumber(m.efficiency, 2)} unit="×" tone="neutral" hint={m.capacity_hours_per_day ? `${formatHours(m.capacity_hours_per_day)} / day` : undefined} />
                <KpiCard label="Downtime windows" value={downtime.length} tone={downtime.length > 0 ? "at-risk" : "ready"} />
                <KpiCard label="Available from" value={formatDateTime(m.available_from, "dd MMM HH:mm")} tone="neutral" />
              </div>

              <Section
                title="Day timeline"
                actions={
                  <>
                    <button type="button" className="btn btn-sm" onClick={() => setDay((x) => addDays(x, -1))}>
                      ‹
                    </button>
                    <span className="num text-sm">{formatDateTime(day, "EEE dd MMM")}</span>
                    <button type="button" className="btn btn-sm" onClick={() => setDay((x) => addDays(x, 1))}>
                      ›
                    </button>
                    <button type="button" className="btn btn-sm" onClick={() => setDay(new Date())}>
                      today
                    </button>
                  </>
                }
              >
                <Timeline entries={dayEntries} day={day} now={now} downtime={downtime} onEntryClick={(e) => navigate(routes.orderDetail(encodeURIComponent(e.order_id)))} />
              </Section>

              <div className="grid grid-main-side">
                <div className="col gap-3">
                  <Section title="Sequence" count={dayEntries.length} flush>
                    <DataTable
                      rows={dayEntries}
                      columns={entryColumns}
                      rowKey={(e) => e.entry_id}
                      onRowClick={(e) => navigate(routes.orderDetail(encodeURIComponent(e.order_id)))}
                      rowClassName={(e) => (e.expected_lateness_hours && e.expected_lateness_hours > 0 ? "row-late" : e.locked ? "row-hold" : undefined)}
                      initialSort={{ key: "start", direction: "asc" }}
                      pageSize={100}
                      dense
                      emptyMessage="Nothing scheduled on this machine"
                    />
                  </Section>
                  <Section title="Eligible queue" count={d.queue.length} flush>
                    <DataTable
                      rows={d.queue}
                      columns={[orderIdColumn(), partColumn(), customerColumn(), scoreColumn(), readinessColumn(), dueColumn(now)]}
                      rowKey={(o) => o.order_id}
                      onRowClick={(o) => navigate(routes.orderDetail(encodeURIComponent(o.order_id)))}
                      rowClassName={(o) => orderRowClass(o, now)}
                      initialSort={{ key: "score", direction: "desc" }}
                      pageSize={50}
                      dense
                      emptyMessage="No orders waiting for this machine"
                    />
                  </Section>
                </div>
                <div className="col gap-3">
                  <Section title="Capability">
                    <dl className="kv">
                      <dt>Processes</dt>
                      <dd>{[m.process_type, ...m.compatible_processes.filter((p) => p !== m.process_type)].map(humanize).join(", ")}</dd>
                      <dt>Materials</dt>
                      <dd>{m.compatible_materials.length ? m.compatible_materials.join(", ") : "any"}</dd>
                      <dt>Tooling mounted</dt>
                      <dd>{m.tooling_configuration.length ? m.tooling_configuration.join(", ") : "—"}</dd>
                      <dt>Current setup</dt>
                      <dd>
                        {m.current_setup_family ?? "—"} / {m.current_material_id ?? "—"}
                      </dd>
                      <dt>Max part (mm)</dt>
                      <dd className="num">{m.max_part_size_mm ? m.max_part_size_mm.join(" × ") : "—"}</dd>
                      <dt>Calendar</dt>
                      <dd>{m.calendar_id ?? "plant default"}</dd>
                      <dt>Preferred rank</dt>
                      <dd className="num">{m.preferred_rank}</dd>
                    </dl>
                  </Section>
                  <Section title="Downtime" count={downtime.length} flush>
                    <DataTable rows={downtime} columns={downtimeColumns} rowKey={(w) => `${w.start}-${w.end}`} dense pageSize={50} emptyMessage="No downtime windows" initialSort={{ key: "start", direction: "asc" }} />
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
