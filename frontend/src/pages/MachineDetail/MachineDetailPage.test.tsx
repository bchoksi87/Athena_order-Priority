import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MachineScheduleResponse } from "@/api/types";
import { machineDetail } from "@/test/apiFixtures";
import { makeEntry } from "@/test/fixtures";
import { authHandlers, mockApi, renderPage } from "@/test/utils";

import MachineDetailPage from "./MachineDetailPage";

function todayAt(hours: number, minutes = 0): string {
  const d = new Date();
  d.setHours(hours, minutes, 0, 0);
  return d.toISOString();
}

describe("MachineDetailPage", () => {
  beforeEach(() => {
    localStorage.clear();
    const schedule: MachineScheduleResponse = {
      machine_id: "MC-LATHE-01",
      machine_name: "TL-250 #1",
      version_number: 4,
      status: "draft",
      start: todayAt(0),
      end: todayAt(23, 59),
      entries: [
        makeEntry({ entry_id: "e1", order_id: "SO-ONTIME", setup_start: todayAt(8), start: todayAt(8, 30), end: todayAt(10), expected_lateness_hours: null, due_date: todayAt(23) }),
        makeEntry({ entry_id: "e2", order_id: "SO-LATE", setup_start: todayAt(10), start: todayAt(10, 15), end: todayAt(13), expected_lateness_hours: 6.5, due_date: todayAt(9) }),
      ],
      downtime: [],
      locks: [],
    };
    mockApi({
      ...authHandlers("operator"),
      "GET /api/v1/machines/MC-LATHE-01": machineDetail,
      "GET /api/v1/machines/MC-LATHE-01/schedule": schedule,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the machine header, load KPIs, calendar and a day timeline with lateness colouring", async () => {
    renderPage(<MachineDetailPage />, { role: "operator", route: "/machines/MC-LATHE-01", path: "/machines/:machineId" });
    expect(await screen.findByRole("heading", { name: "MC-LATHE-01 — TL-250 #1" })).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.getByText("Two-shift weekday calendar", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Spindle service")).toBeInTheDocument();
    const timeline = await screen.findByTestId("timeline");
    const bars = within(timeline).getAllByTestId("timeline-entry");
    expect(bars).toHaveLength(2);
    const late = bars.find((b) => b.textContent?.includes("SO-LATE"))!;
    expect(late.querySelector(".timeline-bar")).toHaveAttribute("fill", "var(--status-late)");
    const rows = Array.from(screen.getAllByRole("table")[0]!.querySelectorAll<HTMLElement>("tbody tr"));
    const lateRow = rows.find((r) => r.textContent?.includes("SO-LATE"))!;
    expect(lateRow).toHaveClass("row-late");
    expect(within(lateRow).getByText("+6.5h")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Machine schedule" })).toHaveAttribute("href", "/machines?machine_id=MC-LATHE-01");
  });
});
