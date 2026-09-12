import { describe, expect, it } from "vitest";

import { makeEntry } from "@/test/fixtures";
import type { GanttBlock } from "@/api/types";

import { idleGaps, machineLines } from "./scheduleGaps";

function block(overrides: Parameters<typeof makeEntry>[0]): GanttBlock {
  const entry = makeEntry(overrides);
  return { entry, order_id: entry.order_id, customer_id: entry.customer_id, customer_name: null, part_id: null, part_name: null, order_status: null, setup_start: entry.setup_start, start: entry.start, end: entry.end, locked: entry.locked, late: false };
}

describe("scheduleGaps", () => {
  it("finds idle gaps between consecutive entries", () => {
    const a = makeEntry({ entry_id: "a", setup_start: "2026-09-11T08:00:00Z", start: "2026-09-11T08:30:00Z", end: "2026-09-11T10:30:00Z" });
    const b = makeEntry({ entry_id: "b", setup_start: "2026-09-11T12:30:00Z", start: "2026-09-11T12:30:00Z", end: "2026-09-11T15:00:00Z" });
    const gaps = idleGaps([b, a], 15);
    expect(gaps).toHaveLength(1);
    expect(gaps[0]?.minutes).toBe(120);
  });

  it("produces the spec's machine lines in order", () => {
    const lines = machineLines(
      [
        block({ entry_id: "a", order_id: "1045", setup_start: "2026-09-11T08:00:00Z", start: "2026-09-11T08:30:00Z", end: "2026-09-11T10:30:00Z" }),
        block({ entry_id: "b", order_id: "1052", setup_start: "2026-09-11T10:30:00Z", start: "2026-09-11T10:30:00Z", end: "2026-09-11T12:00:00Z", setup_minutes: 0 }),
        block({ entry_id: "c", order_id: "1078", setup_start: "2026-09-11T12:30:00Z", start: "2026-09-11T12:30:00Z", end: "2026-09-11T15:00:00Z", setup_minutes: 0 }),
      ],
      [],
      15,
    );
    expect(lines.map((l) => l.label)).toEqual(["Setup — Job 1045", "Job 1045", "Job 1052", "Break / idle — 30 min", "Job 1078"]);
  });
});
