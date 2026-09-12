import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHandlers, mockApi, renderPage } from "@/test/utils";
import { bottleneckReport, kpiReport, otdReport, scheduleQualityReport } from "@/test/scheduleFixtures";

import ExecutiveDashboardPage from "./ExecutiveDashboardPage";

describe("ExecutiveDashboardPage", () => {
  beforeEach(() => {
    localStorage.clear();
    mockApi({
      ...authHandlers("executive"),
      "GET /api/v1/analytics/kpis": kpiReport,
      "GET /api/v1/analytics/on-time-delivery": otdReport,
      "GET /api/v1/analytics/schedule-quality": scheduleQualityReport,
      "GET /api/v1/analytics/bottlenecks": bottleneckReport,
      "GET /api/v1/alerts/summary": { total_active: 12, unacknowledged: 5, by_severity: { critical: 2, high: 3, warning: 4, info: 3 } },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders every executive KPI from GET /analytics/kpis", async () => {
    renderPage(<ExecutiveDashboardPage />, { role: "executive", route: "/dashboard" });
    const delivery = await screen.findByTestId("kpi-delivery");
    await within(delivery).findByText("3,853");
    expect(within(delivery).getByText("59,365")).toBeInTheDocument();
    expect(within(delivery).getByText("Orders due tomorrow").parentElement).toHaveTextContent("118");
    expect(within(delivery).getByText("Overdue orders").parentElement).toHaveTextContent("373");
    expect(within(delivery).getByText("At-risk orders").parentElement).toHaveTextContent("3,515");
    expect(within(delivery).getByText("On-time delivery %").parentElement).toHaveTextContent("84%");
    const capacity = screen.getByTestId("kpi-capacity");
    expect(within(capacity).getByText("Revenue at risk").parentElement).toHaveTextContent("₹23.9 Cr");
    expect(within(capacity).getByText("Margin at risk").parentElement).toHaveTextContent("₹6.3 Cr");
    expect(within(capacity).getByText("Machine utilisation").parentElement).toHaveTextContent("69%");
    expect(within(capacity).getByText("Capacity utilisation").parentElement).toHaveTextContent("322%");
    const blocked = screen.getByTestId("kpi-blocked");
    expect(within(blocked).getByText("Blocked by material").parentElement).toHaveTextContent("370");
    expect(within(blocked).getByText("Blocked by tooling").parentElement).toHaveTextContent("192");
    expect(within(blocked).getByText("Blocked by machine").parentElement).toHaveTextContent("74");
    expect(within(blocked).getByText("Waiting for approval").parentElement).toHaveTextContent("74");
    expect(screen.getByText(/1,117 orders blocked in total/)).toBeInTheDocument();
  });

  it("shows the active plan, the top bottleneck, OTD breakdowns and alert severities", async () => {
    renderPage(<ExecutiveDashboardPage />, { role: "executive", route: "/dashboard" });
    const plan = await screen.findByTestId("plan-version-card");
    expect(plan).toHaveTextContent("v4");
    expect(plan).toHaveTextContent("34");
    expect(plan).toHaveTextContent("by planner");
    const bottleneck = await screen.findByTestId("bottleneck-card");
    expect(bottleneck).toHaveTextContent("Additive 3D Printing");
    expect(bottleneck).toHaveTextContent("530%");
    expect(bottleneck).toHaveTextContent("772");
    const severities = await screen.findByTestId("alert-severities");
    expect(within(severities).getByText("critical").parentElement).toHaveTextContent("2");
    const byTier = await screen.findByTestId("otd-by-tier");
    expect(byTier).toHaveTextContent("Strategic");
    expect(byTier).toHaveTextContent("30%");
    expect(screen.getByText(/On-time delivery: 28% over the last 30 days/)).toBeInTheDocument();
  });
});
