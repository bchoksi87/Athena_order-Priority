import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHandlers, mockApi, renderPage, type MockApi } from "@/test/utils";
import { explanationLines, machineOptions, orderDetail, priorityConfiguration } from "@/test/apiFixtures";

import OrderDetailPage from "./OrderDetailPage";

describe("OrderDetailPage", () => {
  let api: MockApi;

  beforeEach(() => {
    localStorage.clear();
    api = mockApi({
      ...authHandlers("production_manager"),
      "GET /api/v1/orders/SO2609-00093-02": orderDetail,
      "GET /api/v1/orders/SO2609-00093-02/machines": machineOptions,
      "GET /api/v1/priority/configuration": priorityConfiguration,
      "GET /api/v1/machines": [],
      "POST /api/v1/orders/SO2609-00093-02/expedite": (req) => ({ expedite_id: "exp_1", order_id: "SO2609-00093-02", boost_points: (req.body as { boost_points: number }).boost_points, starts_at: "2026-09-12T06:00:00Z", expires_at: "2026-09-12T10:00:00Z", reason: (req.body as { reason: string }).reason, created_by: "usr_production_manager", created_at: "2026-09-12T06:00:00Z", active: true }),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the API explanation lines exactly, in order, with the total", async () => {
    renderPage(<OrderDetailPage />, { role: "production_manager", route: "/orders/SO2609-00093-02", path: "/orders/:orderId" });
    const panel = await screen.findByTestId("explanation-lines");
    const items = within(panel).getAllByRole("listitem");
    expect(items).toHaveLength(explanationLines.length);
    const expected = ["+28Due Date Urgency: Due in 18 hours", "+20SLA Risk: SLA 48 h: 9 hours remaining (19%)", "+15Customer Importance: Strategic customer", "+8Order Value: ₹7.9 L (top 20%)", "+10Production Readiness: Material and tooling available", "+7Machine Availability: MC-LATHE-01 free now", "-3Setup Efficiency: Large setup required (93 min)", "+6Aging: Waiting 8 days (3 beyond 5)"];
    // Bonuses first, then penalties, then adjustments — every line verbatim from the API.
    const texts = items.map((li) => `${li.querySelector(".explain-points")?.textContent}${li.querySelector(".explain-name")?.textContent}${li.querySelector(".explain-reason")?.textContent}`);
    expect(texts).toEqual([expected[0], expected[1], expected[2], expected[3], expected[4], expected[5], expected[6], expected[7]]);
    expect(within(panel).getByTestId("explanation-lines-total")).toHaveTextContent("91");
    expect(screen.getByText("Machine options")).toBeInTheDocument();
    expect(await screen.findByText("RECOMMENDED")).toBeInTheDocument();
  });

  it("requires a reason before an expedite is sent, then posts the exact body", async () => {
    renderPage(<OrderDetailPage />, { role: "production_manager", route: "/orders/SO2609-00093-02", path: "/orders/:orderId" });
    await screen.findByTestId("explanation-lines");
    fireEvent.click(screen.getByRole("button", { name: "Expedite" }));
    const dialog = await screen.findByRole("dialog", { name: /Expedite order/ });
    fireEvent.click(within(dialog).getByTestId("action-submit"));
    expect(await within(dialog).findByText(/A reason of at least 3 characters is required/)).toBeInTheDocument();
    expect(api.find("POST", "/api/v1/orders/SO2609-00093-02/expedite")).toHaveLength(0);

    fireEvent.change(within(dialog).getByPlaceholderText(/Why is this action needed/), { target: { value: "Customer line down" } });
    fireEvent.change(within(dialog).getByLabelText(/Boost points/), { target: { value: "45" } });
    fireEvent.click(within(dialog).getByTestId("action-submit"));
    await waitFor(() => expect(api.find("POST", "/api/v1/orders/SO2609-00093-02/expedite")).toHaveLength(1));
    const call = api.find("POST", "/api/v1/orders/SO2609-00093-02/expedite")[0]!;
    expect(call.body).toEqual({ reason: "Customer line down", boost_points: 45, duration_hours: 4 });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Expedite order/ })).not.toBeInTheDocument());
    expect(await screen.findByText(/Expedite order: SO2609-00093-02/)).toBeInTheDocument();
  });
});
