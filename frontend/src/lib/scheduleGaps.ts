/**
 * Derives idle / non-working gaps between consecutive blocks of one machine. The API exposes
 * downtime windows per machine but not the calendar breaks, so the board shades the gaps it can
 * see in the plan itself (spec Phase 8 MACHINE VIEW "12:00 — Break").
 */
import type { GanttBlock, ScheduleEntry, TimeWindow } from "@/api/types";
import { formatDateTime, parseUtc } from "./time";

export interface IdleGap {
  start: Date;
  end: Date;
  minutes: number;
}

/** Gaps of at least `minMinutes` between the end of one entry and the setup start of the next. */
export function idleGaps(entries: ScheduleEntry[], minMinutes = 15): IdleGap[] {
  const sorted = [...entries]
    .map((e) => ({ s: parseUtc(e.setup_start) ?? parseUtc(e.start), e: parseUtc(e.end) }))
    .filter((x): x is { s: Date; e: Date } => x.s !== null && x.e !== null)
    .sort((a, b) => a.s.getTime() - b.s.getTime());
  const gaps: IdleGap[] = [];
  let cursor: Date | null = null;
  for (const item of sorted) {
    if (cursor && item.s.getTime() - cursor.getTime() >= minMinutes * 60_000) {
      gaps.push({ start: cursor, end: item.s, minutes: (item.s.getTime() - cursor.getTime()) / 60_000 });
    }
    if (!cursor || item.e > cursor) cursor = item.e;
  }
  return gaps;
}

/** Gaps as TimeWindows (for the Gantt/Timeline shading), clipped to the axis when given. */
export function idleWindows(entries: ScheduleEntry[], minMinutes = 15, axisStart?: Date, axisEnd?: Date): TimeWindow[] {
  return idleGaps(entries, minMinutes)
    .map((g) => ({
      start: (axisStart && g.start < axisStart ? axisStart : g.start).toISOString(),
      end: (axisEnd && g.end > axisEnd ? axisEnd : g.end).toISOString(),
      reason: `Idle ${Math.round(g.minutes)} min (no job placed)`,
    }))
    .filter((w) => w.start < w.end);
}

export type MachineLineKind = "setup" | "job" | "downtime" | "idle";

/** One line of the spec's machine list: "08:00 — Setup — Job 1045". */
export interface MachineLine {
  kind: MachineLineKind;
  at: Date;
  end: Date;
  label: string;
  entry: ScheduleEntry | null;
  block: GanttBlock | null;
  reason: string | null;
}

/** Chronological lines for one machine: setup + job per block, downtime windows and idle gaps. */
export function machineLines(blocks: GanttBlock[], downtime: TimeWindow[], minIdleMinutes = 15): MachineLine[] {
  const lines: MachineLine[] = [];
  for (const b of blocks) {
    const setupStart = parseUtc(b.entry.setup_start) ?? parseUtc(b.entry.start);
    const start = parseUtc(b.entry.start);
    const end = parseUtc(b.entry.end);
    if (!setupStart || !start || !end) continue;
    if (b.entry.setup_minutes > 0 && setupStart < start) {
      lines.push({ kind: "setup", at: setupStart, end: start, label: `Setup — Job ${b.order_id}`, entry: b.entry, block: b, reason: b.entry.setup_family ? `family ${b.entry.setup_family}` : null });
    }
    lines.push({ kind: "job", at: start, end, label: `Job ${b.order_id}`, entry: b.entry, block: b, reason: b.entry.placement_reason });
  }
  for (const w of downtime) {
    const s = parseUtc(w.start);
    const e = parseUtc(w.end);
    if (s && e) lines.push({ kind: "downtime", at: s, end: e, label: w.reason ? `Downtime — ${w.reason}` : "Downtime", entry: null, block: null, reason: w.reason || null });
  }
  for (const g of idleGaps(blocks.map((b) => b.entry), minIdleMinutes)) {
    lines.push({ kind: "idle", at: g.start, end: g.end, label: `Break / idle — ${Math.round(g.minutes)} min`, entry: null, block: null, reason: null });
  }
  return lines.sort((a, b) => a.at.getTime() - b.at.getTime() || (a.kind === "setup" ? -1 : 1));
}

/** The spec's list line: "08:00 — Setup — Job 1045". */
export function formatMachineLine(line: MachineLine): string {
  return `${formatDateTime(line.at, "HH:mm")} — ${line.label}`;
}
