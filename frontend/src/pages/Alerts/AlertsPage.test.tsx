import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHandlers, mockApi, renderPage, type MockApi } from "@/test/utils";
import { makeAlert, pageOf } from "@/test/apiFixtures";

import AlertsPage from "./AlertsPage";

describe("AlertsPage", () => {
  let api: MockApi;
  let acknowledged = false;

  beforeEach(() => {
    localStorage.clear();
    acknowledged = false;
    api = mockApi({
      ...authHandlers("supervisor"),
      "GET /api/v1/alerts": () => pageOf([makeAlert({ acknowledged, acknowledged_by: acknowledged ? "supervisor" : null }), makeAlert({ alert_id: "alt_2", severity: "critical", alert_type: "machine_downtime", title: "MC-LATHE-01 down", order_id: null, machine_id: "MC-LATHE-01" })]),
      "GET /api/v1/alerts/summary": { total_active: 2, unacknowledged: 2, by_severity: { info: 0, warning: 0, high: 1, critical: 1 } },
      "POST /api/v1/alerts/alt_1/acknowledge": (req) => {
        acknowledged = true;
        return makeAlert({ acknowledged: true, acknowledged_by: "supervisor", details: { note: (req.body as { note: string | null }).note } });
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists alerts with severity, refs and recommended action, and acknowledges with a note", async () => {
    renderPage(<AlertsPage />, { role: "supervisor", route: "/alerts" });
    const table = await screen.findByRole("table", { name: "Alerts" });
    expect(within(table).getByText("MC-LATHE-01 down")).toBeInTheDocument();
    expect(within(table).getAllByText("Expedite or move to MC-LATHE-02")).toHaveLength(2);
    expect(within(table).getByRole("link", { name: "SO2609-00093-02" })).toHaveAttribute("href", "/orders/SO2609-00093-02");
    const summary = screen.getByTestId("alert-summary");
    expect(within(summary).getByText("Unacknowledged").parentElement).toHaveTextContent("2");

    const row = within(table).getByText("SO2609-00093-02 likely late").closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Acknowledge" }));
    const dialog = await screen.findByRole("dialog", { name: /Acknowledge: SO2609-00093-02 likely late/ });
    fireEvent.change(within(dialog).getByPlaceholderText(/What was done/), { target: { value: "Moved to MC-LATHE-02" } });
    fireEvent.click(within(dialog).getByTestId("ack-submit"));
    await waitFor(() => expect(api.find("POST", "/api/v1/alerts/alt_1/acknowledge")).toHaveLength(1));
    expect(api.find("POST", "/api/v1/alerts/alt_1/acknowledge")[0]!.body).toEqual({ note: "Moved to MC-LATHE-02" });
    expect(await screen.findByText("Alert acknowledged")).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Alerts" })).getByText(/✓ supervisor/)).toBeInTheDocument());
  });

  it("hides the acknowledge button for roles below supervisor", async () => {
    api.set(authHandlers("operator"));
    renderPage(<AlertsPage />, { role: "operator", route: "/alerts" });
    const table = await screen.findByRole("table", { name: "Alerts" });
    expect(within(table).queryByRole("button", { name: "Acknowledge" })).not.toBeInTheDocument();
  });
});
