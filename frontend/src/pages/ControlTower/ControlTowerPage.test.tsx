import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Role } from "@/api/types";
import { authHandlers, mockApi, renderPage, type MockApi } from "@/test/utils";
import { makeAlert, makeOrderRow, pageOf, priorityInfo } from "@/test/apiFixtures";
import { bottleneckReport, capacityReport, kpiReport, replanOutcome, schedulePlan, scheduleQualityReport } from "@/test/scheduleFixtures";

import ControlTowerPage from "./ControlTowerPage";

function setup(role: Role): MockApi {
  localStorage.clear();
  return mockApi({
    ...authHandlers(role),
    "GET /api/v1/schedule": schedulePlan,
    "GET /api/v1/analytics/schedule-quality": scheduleQualityReport,
    "GET /api/v1/analytics/kpis": kpiReport,
    "GET /api/v1/orders": pageOf([makeOrderRow({}, priorityInfo)]),
    "GET /api/v1/alerts": pageOf([makeAlert()]),
    "GET /api/v1/analytics/bottlenecks": bottleneckReport,
    "GET /api/v1/analytics/capacity": capacityReport,
    "GET /api/v1/data-quality": { run_id: "dq_1", detected_at: null, total_issues: 1, by_severity: {}, by_code: {}, by_entity_type: {}, blocked_entities: 210, engine_summary: {}, dashboard: { as_of: "2026-09-12T13:00:00Z", open_orders: 3853, unschedulable_orders: 210, headline: "210 orders cannot be scheduled because: 93 missing operations", reasons: [], orders_by_code: {}, warnings_by_code: {} } },
    "POST /api/v1/schedule/replan": replanOutcome,
  });
}

describe("ControlTowerPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("plan bar actions by role", () => {
    beforeEach(() => undefined);

    it("offers generate, approve, reject and evaluate replan to a production manager for a draft plan", async () => {
      setup("production_manager");
      renderPage(<ControlTowerPage />, { role: "production_manager", route: "/control-tower" });
      const bar = await screen.findByTestId("plan-bar");
      await within(bar).findByRole("button", { name: "Approve v4" });
      expect(within(bar).getByRole("button", { name: "Generate schedule" })).toBeInTheDocument();
      expect(within(bar).getByRole("button", { name: "Reject v4" })).toBeInTheDocument();
      expect(within(bar).getByRole("button", { name: "Evaluate replan" })).toBeInTheDocument();
      expect(within(bar).queryByRole("button", { name: "Publish v4" })).not.toBeInTheDocument();
      expect(bar).toHaveTextContent("v4");
    });

    it("lets a planner generate and evaluate but not approve", async () => {
      setup("planner");
      renderPage(<ControlTowerPage />, { role: "planner", route: "/control-tower" });
      const bar = await screen.findByTestId("plan-bar");
      await within(bar).findByRole("button", { name: "Generate schedule" });
      expect(within(bar).getByRole("button", { name: "Evaluate replan" })).toBeInTheDocument();
      expect(within(bar).queryByRole("button", { name: "Approve v4" })).not.toBeInTheDocument();
      expect(within(bar).queryByRole("button", { name: "Reject v4" })).not.toBeInTheDocument();
    });

    it("shows no workflow actions to an operator", async () => {
      setup("operator");
      renderPage(<ControlTowerPage />, { role: "operator", route: "/control-tower" });
      const bar = await screen.findByTestId("plan-bar");
      await within(bar).findByText(/Active plan/);
      expect(within(bar).queryByRole("button", { name: "Generate schedule" })).not.toBeInTheDocument();
      expect(within(bar).queryByRole("button", { name: "Evaluate replan" })).not.toBeInTheDocument();
      expect(within(bar).queryByRole("button", { name: "Approve v4" })).not.toBeInTheDocument();
    });
  });

  it("evaluates a replan and renders the decision with the old-vs-new comparison", async () => {
    const api = setup("production_manager");
    renderPage(<ControlTowerPage />, { role: "production_manager", route: "/control-tower" });
    const bar = await screen.findByTestId("plan-bar");
    fireEvent.click(await within(bar).findByRole("button", { name: "Evaluate replan" }));
    const result = await screen.findByTestId("replan-result");
    await waitFor(() => expect(api.find("POST", "/api/v1/schedule/replan")).toHaveLength(1));
    expect(api.find("POST", "/api/v1/schedule/replan")[0]?.body).toEqual({ trigger: "manual", reason: "Evaluated from the control tower" });
    expect(result).toHaveTextContent("Candidate v5 improves quality by 9.9%");
    expect(result).toHaveTextContent("9.9%");
    expect(result).toHaveTextContent("Requires approval");
    const table = within(result).getByTestId("comparison-table");
    const onTime = table.querySelector('tr[data-metric="on_time_pct"]');
    expect(onTime).toHaveTextContent("On-time delivery");
    expect(onTime).toHaveTextContent("87%");
    expect(onTime).toHaveTextContent("94%");
    expect(onTime).toHaveClass("cmp-better");
    const lateness = table.querySelector('tr[data-metric="avg_lateness_hours"]');
    expect(lateness).toHaveTextContent("8.2h");
    expect(lateness).toHaveTextContent("2.4h");
    expect(table.querySelector('tr[data-metric="total_setup_hours"]')).toHaveTextContent("126h");
    expect(within(result).getByRole("button", { name: "Approve candidate v5" })).toBeInTheDocument();
    // The recommended actions and the data-quality headline complete the morning picture.
    expect(await screen.findByTestId("recommended-actions")).toHaveTextContent("Expedite or move to MC-LATHE-02");
    expect(screen.getByTestId("dq-headline")).toHaveTextContent("210 orders cannot be scheduled");
  });
});
