/** Semantic tone of a schedule entry, shared by GanttChart and Timeline. */
import type { ScheduleEntry } from "@/api/types";
import type { Tone } from "@/lib/constants";
import { parseUtc } from "@/lib/time";

/** Derives the semantic tone of an entry from lateness, lock state and due-date slack. */
export function entryTone(entry: ScheduleEntry, now: Date, atRiskSlackHours: number): Tone {
  const end = parseUtc(entry.end);
  if (entry.expected_lateness_hours !== null && entry.expected_lateness_hours > 0) return "late";
  if (end && end < now) return "done";
  if (entry.due_date && end) {
    const due = parseUtc(entry.due_date);
    if (due && (due.getTime() - end.getTime()) / 3_600_000 < atRiskSlackHours) return "at-risk";
  }
  if (entry.locked) return "hold";
  return "running";
}

