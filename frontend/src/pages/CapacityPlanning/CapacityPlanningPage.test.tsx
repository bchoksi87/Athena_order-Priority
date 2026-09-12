import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHandlers, mockApi, renderPage } from "@/test/utils";
import { capacityReport } from "@/test/scheduleFixtures";

import CapacityPlanningPage from "./CapacityPlanningPage";

describe("CapacityPlanningPage", () => {
  beforeEach(() => {
    localStorage.clear();
    mockApi({ ...authHandlers("executive"), "GET /api/v1/analytics/capacity": capacityReport });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the Required / Available / Gap table with the gap computed and highlighted", async () => {
    renderPage(<CapacityPlanningPage />, { role: "executive", route: "/capacity?dimension=process" });
    const table = await screen.findByRole("table", { name: "Capacity totals" });
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    const byKey = Object.fromEntries(rows.map((r) => [r.getAttribute("data-rowkey"), r]));
    // Sorted by gap ascending: the shortfall comes first and is flagged.
    expect(rows[0]).toHaveAttribute("data-rowkey", "cnc_5axis");
    expect(byKey.cnc_5axis).toHaveClass("row-late");
    expect(byKey.cnc_5axis).toHaveTextContent("940h");
    expect(byKey.cnc_5axis).toHaveTextContent("780h");
    expect(byKey.cnc_5axis).toHaveTextContent("−160h");
    expect(byKey.cnc_5axis).toHaveTextContent("121%");
    expect(byKey.cnc_3axis).toHaveTextContent("+80h");
    expect(byKey.cnc_3axis).not.toHaveClass("row-late");
    expect(byKey.sla).toHaveTextContent("+190h");
    const kpis = screen.getByTestId("capacity-kpis");
    expect(within(kpis).getByText("Net gap").parentElement).toHaveTextContent("+110h");
    expect(within(kpis).getByText("Overloaded").parentElement).toHaveTextContent("1");
    expect(within(kpis).getByText("Unallocated").parentElement).toHaveTextContent("13h");
    expect(screen.getByText(/withheld by data quality carry no demand/)).toBeInTheDocument();
  });
});
