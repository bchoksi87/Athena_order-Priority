import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { makeEntry } from "@/test/fixtures";

import { entryTone } from "@/lib/entryTone";

import { GanttChart } from "./GanttChart";

const start = new Date("2026-09-11T00:00:00Z");
const end = new Date("2026-09-13T00:00:00Z");
const rows = [
  { id: "CNC-01", label: "CNC-01", sublabel: "5-axis" },
  { id: "CNC-02", label: "CNC-02" },
];

describe("GanttChart", () => {
  it("renders one bar group per entry on a known row, with setup blocks", () => {
    const entries = [
      makeEntry({ entry_id: "e1", machine_id: "CNC-01" }),
      makeEntry({ entry_id: "e2", machine_id: "CNC-02", setup_start: "2026-09-11T12:00:00Z", start: "2026-09-11T12:00:00Z", end: "2026-09-11T14:00:00Z" }),
      makeEntry({ entry_id: "e3", machine_id: "UNKNOWN" }),
    ];
    render(<GanttChart rows={rows} entries={entries} start={start} end={end} zoom="day" now={new Date("2026-09-11T09:00:00Z")} />);
    const groups = screen.getAllByTestId("gantt-entry");
    expect(groups).toHaveLength(2);
    expect(groups[0]?.querySelector(".gantt-setup")).not.toBeNull();
    expect(groups[1]?.querySelector(".gantt-setup")).toBeNull();
    expect(screen.getByTestId("gantt-now")).toBeInTheDocument();
    expect(screen.getByText("2 machines")).toBeInTheDocument();
  });

  it("positions bars according to the time scale", () => {
    render(<GanttChart rows={rows} entries={[makeEntry()]} start={start} end={end} zoom="day" showLegend={false} />);
    const bar = screen.getByTestId("gantt-entry").querySelector(".gantt-bar");
    // day zoom = 56 px/h; run starts 8.5h after the scale start and lasts 3h.
    expect(Number(bar?.getAttribute("x"))).toBeCloseTo(8.5 * 56, 5);
    expect(Number(bar?.getAttribute("width"))).toBeCloseTo(3 * 56, 5);
  });

  it("fires click handler and shows a tooltip on hover", () => {
    const onEntryClick = vi.fn();
    const entry = makeEntry();
    render(<GanttChart rows={rows} entries={[entry]} start={start} end={end} onEntryClick={onEntryClick} />);
    const bar = screen.getByTestId("gantt-entry").querySelector(".gantt-bar") as SVGRectElement;
    fireEvent.mouseEnter(bar, { clientX: 10, clientY: 10 });
    expect(screen.getByRole("tooltip")).toHaveTextContent("Highest priority ready on CNC-01");
    fireEvent.click(bar);
    expect(onEntryClick).toHaveBeenCalledWith(entry);
  });

  it("derives tones from lateness, slack and lock state", () => {
    const now = new Date("2026-09-11T00:00:00Z");
    expect(entryTone(makeEntry({ expected_lateness_hours: 2 }), now, 8)).toBe("late");
    expect(entryTone(makeEntry({ due_date: "2026-09-11T13:00:00Z" }), now, 8)).toBe("at-risk");
    expect(entryTone(makeEntry({ locked: true }), now, 8)).toBe("hold");
    expect(entryTone(makeEntry(), now, 8)).toBe("running");
    expect(entryTone(makeEntry(), new Date("2026-09-12T00:00:00Z"), 8)).toBe("done");
  });

  it("handles 120 rows without dropping entries", () => {
    const many = Array.from({ length: 120 }, (_, i) => ({ id: `M-${i}`, label: `M-${i}` }));
    const entries = many.map((r, i) => makeEntry({ entry_id: `e${i}`, machine_id: r.id }));
    render(<GanttChart rows={many} entries={entries} start={start} end={end} showLegend={false} />);
    expect(screen.getAllByTestId("gantt-entry")).toHaveLength(120);
  });
});

describe("GanttChart compare mode", () => {
  it("draws ghost bars for the baseline's moved and removed entries and marks moved / added bars", () => {
    const current = [
      makeEntry({ entry_id: "e1", operation_id: "op1", machine_id: "CNC-02" }), // moved from CNC-01
      makeEntry({ entry_id: "e2", operation_id: "op2", machine_id: "CNC-01", start: "2026-09-11T14:00:00Z", end: "2026-09-11T16:00:00Z", setup_start: "2026-09-11T14:00:00Z" }), // unchanged
      makeEntry({ entry_id: "e4", operation_id: "op4", machine_id: "CNC-02", start: "2026-09-12T08:00:00Z", end: "2026-09-12T10:00:00Z", setup_start: "2026-09-12T08:00:00Z" }), // added
    ];
    const baselineEntries = [
      makeEntry({ entry_id: "e1", operation_id: "op1", machine_id: "CNC-01" }),
      makeEntry({ entry_id: "e2", operation_id: "op2", machine_id: "CNC-01", start: "2026-09-11T14:00:00Z", end: "2026-09-11T16:00:00Z", setup_start: "2026-09-11T14:00:00Z" }),
      makeEntry({ entry_id: "e3", operation_id: "op3", machine_id: "CNC-02", start: "2026-09-12T12:00:00Z", end: "2026-09-12T13:00:00Z", setup_start: "2026-09-12T12:00:00Z" }), // removed
    ];
    render(<GanttChart rows={rows} entries={current} baselineEntries={baselineEntries} baselineLabel="v3" start={start} end={end} zoom="day" showLegend />);
    const ghosts = screen.getAllByTestId("gantt-ghost");
    expect(ghosts).toHaveLength(2);
    expect(ghosts.map((g) => g.getAttribute("data-entry-id")).sort()).toEqual(["e1", "e3"]);
    expect(screen.getAllByTestId("gantt-ghost").find((g) => g.getAttribute("data-entry-id") === "e3")).toHaveClass("gantt-ghost-removed");
    const bars = screen.getAllByTestId("gantt-entry");
    const change = Object.fromEntries(bars.map((b) => [b.getAttribute("data-entry-id"), b.getAttribute("data-change")]));
    expect(change).toEqual({ e1: "moved", e2: null, e4: "added" });
    expect(screen.getByText("v3 (moved)")).toBeInTheDocument();
    fireEvent.mouseEnter(ghosts[0]!, { clientX: 5, clientY: 5 });
    expect(screen.getByRole("tooltip")).toHaveTextContent(/vs v3/);
  });

  it("clusters touching tiny bars at low zoom", () => {
    const tiny = Array.from({ length: 20 }, (_, i) => {
      const s = new Date(Date.UTC(2026, 8, 11, 0, i * 6));
      const e = new Date(s.getTime() + 5 * 60_000);
      return makeEntry({ entry_id: `t${i}`, operation_id: `top${i}`, machine_id: "CNC-01", setup_start: s.toISOString(), start: s.toISOString(), end: e.toISOString() });
    });
    render(<GanttChart rows={rows} entries={tiny} start={start} end={end} zoom="fortnight" showLegend={false} />);
    expect(screen.queryAllByTestId("gantt-entry")).toHaveLength(0);
    const cluster = screen.getByTestId("gantt-cluster");
    expect(cluster).toHaveAttribute("data-count", "20");
  });
});
