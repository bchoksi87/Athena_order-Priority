import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHandlers, mockApi, renderPage, type MockApi } from "@/test/utils";
import { explanationResponse, makeOrderRow, pageOf, priorityInfo } from "@/test/apiFixtures";

import PriorityQueuePage from "./PriorityQueuePage";

const rows = [
  makeOrderRow({}, priorityInfo),
  makeOrderRow({ order_id: "SO2609-00093-03", part_name: "Nozzle Rev A", customer_name: "Agni Components Ltd" }, { ...priorityInfo, score: 64, rank: 2, risk_level: "medium", readiness: "waiting_material", blocked: true, blocking_reasons: ["Material MAT-RS-GREY short by 4 kg"] }),
];

describe("PriorityQueuePage", () => {
  let api: MockApi;

  beforeEach(() => {
    localStorage.clear();
    api = mockApi({
      ...authHandlers("production_manager"),
      "GET /api/v1/orders": (req) => (req.url.searchParams.get("risk") === "critical" ? pageOf([rows[0]!], 1, 50, 1) : pageOf(rows, 1, 50, 277)),
      "GET /api/v1/customers": pageOf([]),
      "GET /api/v1/machines": [],
      "GET /api/v1/expedites": [],
      "GET /api/v1/orders/SO2609-00093-02/explanation": explanationResponse,
      "GET /api/v1/priority/configuration": { version: null, profile: { expedite: { default_boost_points: 30, max_boost_points: 60, default_duration_hours: 4, max_duration_hours: 72 } }, weights_pct: {} },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the server page with rank, score, readiness and blocked rows", async () => {
    renderPage(<PriorityQueuePage />, { role: "production_manager", route: "/priority-queue" });
    const table = await screen.findByRole("table", { name: "Priority queue" });
    const body = table.querySelector("tbody");
    const trs = Array.from(body?.querySelectorAll("tr") ?? []);
    expect(trs.map((tr) => tr.getAttribute("data-rowkey"))).toEqual(["SO2609-00093-02", "SO2609-00093-03"]);
    expect(trs[1]).toHaveClass("row-blocked");
    expect(within(trs[0]!).getAllByRole("meter")[0]).toHaveAttribute("aria-valuenow", "91");
    expect(within(trs[1]!).getByText("Waiting material")).toBeInTheDocument();
    // First request asked the server for the default sort and paging.
    const first = api.find("GET", "/api/v1/orders")[0]!;
    expect(first.url.searchParams.get("sort")).toBe("rank");
    expect(first.url.searchParams.get("order")).toBe("asc");
    expect(first.url.searchParams.get("page")).toBe("1");
    expect(first.url.searchParams.get("page_size")).toBe("50");
    expect(await screen.findByText(/277 · sorted by rank asc/)).toBeInTheDocument();
  });

  it("sorts server-side when a sortable header is clicked", async () => {
    renderPage(<PriorityQueuePage />, { role: "production_manager", route: "/priority-queue" });
    await screen.findByRole("table", { name: "Priority queue" });
    fireEvent.click(screen.getByText("Due"));
    await waitFor(() => {
      const last = api.find("GET", "/api/v1/orders").at(-1)!;
      expect(last.url.searchParams.get("sort")).toBe("due_date");
      expect(last.url.searchParams.get("order")).toBe("asc");
    });
    expect(screen.getByText(/sorted by due_date asc/)).toBeInTheDocument();
  });

  it("applies a filter as a server query parameter", async () => {
    renderPage(<PriorityQueuePage />, { role: "production_manager", route: "/priority-queue" });
    await screen.findByRole("table", { name: "Priority queue" });
    fireEvent.change(screen.getByLabelText("Risk"), { target: { value: "critical" } });
    // The filtered list request carries the parameter (the summary strip issues its own unfiltered count requests).
    await waitFor(() => {
      const filtered = api.find("GET", "/api/v1/orders").filter((r) => r.url.searchParams.get("risk") === "critical");
      expect(filtered.length).toBeGreaterThan(0);
      expect(filtered.at(-1)!.url.searchParams.get("page_size")).toBe("50");
    });
    await waitFor(() => expect(screen.queryByText("SO2609-00093-03")).not.toBeInTheDocument());
  });

  it("opens the Why? drawer with the engine's explanation lines and quick actions", async () => {
    renderPage(<PriorityQueuePage />, { role: "production_manager", route: "/priority-queue" });
    await screen.findByRole("table", { name: "Priority queue" });
    fireEvent.click(screen.getByRole("button", { name: "Why is SO2609-00093-02 prioritised?" }));
    const drawer = await screen.findByTestId("drawer");
    await within(drawer).findByTestId("explanation-lines");
    expect(within(drawer).getByTestId("why-score")).toHaveTextContent("91");
    const lines = within(drawer).getAllByRole("listitem");
    expect(lines[0]).toHaveTextContent("+28");
    expect(lines[0]).toHaveTextContent("Due Date Urgency: Due in 18 hours");
    expect(within(drawer).getByTestId("explanation-lines-total")).toHaveTextContent("91");
    // Quick actions are available to a production manager and open the reason dialog.
    fireEvent.click(within(drawer).getByRole("button", { name: "Actions" }));
    fireEvent.click(within(drawer).getByRole("menuitem", { name: "Expedite" }));
    expect(await screen.findByRole("dialog", { name: /Expedite order/ })).toBeInTheDocument();
  });
});
