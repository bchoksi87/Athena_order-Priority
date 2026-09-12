import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatDateTime } from "@/lib/time";
import { authHandlers, mockApi, renderPage } from "@/test/utils";
import { daySchedule, schedulePlan } from "@/test/scheduleFixtures";

import MachineSchedulePage from "./MachineSchedulePage";

const t = (iso: string) => formatDateTime(iso, "HH:mm");

describe("MachineSchedulePage", () => {
  beforeEach(() => {
    localStorage.clear();
    mockApi({
      ...authHandlers("planner"),
      "GET /api/v1/schedule": schedulePlan,
      "GET /api/v1/schedule/2026-09-11": daySchedule,
      "GET /api/v1/schedule/versions": { items: [schedulePlan.version], total: 1, page: 1, page_size: 100, pages: 1, has_more: false },
      "GET /api/v1/scheduling/configuration": { version: null, scheduling: { at_risk_slack_hours: 8 }, replanning: {}, alerts: {}, data_quality: {} },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("formats the list view in the spec's '08:00 — Setup — Job 1045' format with breaks, downtime, lateness and locks", async () => {
    renderPage(<MachineSchedulePage />, { role: "planner", route: "/machines?view=list&date=2026-09-11" });
    const list = await screen.findByTestId("machine-list");
    const machines = within(list).getAllByTestId("machine-list-item");
    expect(machines).toHaveLength(2);
    const lines = within(machines[0]!).getAllByTestId("machine-line");
    const text = lines.map((l) => `${l.querySelector(".num")?.textContent} — ${l.querySelector(".ms-line-label")?.textContent?.trim()}`);
    expect(text).toEqual([
      `${t("2026-09-11T08:00:00Z")} — Setup — Job 1045`,
      `${t("2026-09-11T08:30:00Z")} — Job 1045`,
      `${t("2026-09-11T10:30:00Z")} — Job 1052`,
      `${t("2026-09-11T12:00:00Z")} — Break / idle — 30 min`,
      `${t("2026-09-11T12:30:00Z")} — Job 1078 🔒`,
      `${t("2026-09-11T16:00:00Z")} — Downtime — preventive maintenance`,
    ]);
    expect(lines[4]).toHaveClass("ms-line-late");
    expect(lines[4]).toHaveClass("ms-line-locked");
    expect(lines[4]).toHaveTextContent("late +4.0h");
    expect(lines[3]).toHaveClass("ms-line-idle");
    expect(machines[1]).toHaveTextContent("Nothing scheduled on this day");
    expect(machines[0]).toHaveTextContent("Machine CNC-01");
  });

  it("renders the day board with one row per machine grouped by machine group", async () => {
    renderPage(<MachineSchedulePage />, { role: "planner", route: "/machines?date=2026-09-11" });
    const gantt = await screen.findByTestId("gantt");
    expect(within(gantt).getByText("2 machines")).toBeInTheDocument();
    expect(within(gantt).getAllByTestId("gantt-entry")).toHaveLength(3);
    expect(gantt.querySelector(".gantt-group")).toHaveTextContent("CNC5");
    expect(gantt.querySelector(".gantt-nonworking")).not.toBeNull();
    expect(gantt.querySelector(".gantt-downtime")).not.toBeNull();
  });
});
