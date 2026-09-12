import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHandlers, mockApi, renderPage, type MockApi } from "@/test/utils";
import { machineDetail, pageOf, priorityConfiguration } from "@/test/apiFixtures";
import { simulationResponse } from "@/test/scheduleFixtures";

import WhatIfSimulationPage from "./WhatIfSimulationPage";
import { SAVED_SCENARIOS_KEY, buildScenario } from "./scenarioSpecs";

const machineRow = { machine: { ...machineDetail.machine, machine_id: "MC-CNC5-01", machine_name: "5-axis #1", machine_group: "CNC5" }, load: machineDetail.load, active_locks: 0 };

describe("WhatIfSimulationPage", () => {
  let api: MockApi;

  beforeEach(() => {
    localStorage.clear();
    api = mockApi({
      ...authHandlers("planner"),
      "GET /api/v1/simulation/scenario-types": { kinds: ["machine_down", "urgent_orders", "add_machine", "extra_working_day", "extra_shift", "outsource", "material_delay", "material_arrival", "prioritize_customer", "weight_change", "due_date_change", "hold_orders", "expedite_orders"].map((kind) => ({ kind, title: kind, description: "", schema: {} })), schema: {} },
      "GET /api/v1/machines": [machineRow],
      "GET /api/v1/customers": pageOf([]),
      "GET /api/v1/orders": pageOf([]),
      "GET /api/v1/priority/configuration": priorityConfiguration,
      "POST /api/v1/schedule/simulate": simulationResponse,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds the exact machine_down request body and renders the diff", async () => {
    renderPage(<WhatIfSimulationPage />, { role: "planner", route: "/simulation" });
    const builder = await screen.findByTestId("scenario-builder");
    const machine = within(builder).getByLabelText("Machine");
    await waitFor(() => expect(within(machine).getAllByRole("option").length).toBeGreaterThan(1));
    fireEvent.change(machine, { target: { value: "MC-CNC5-01" } });
    fireEvent.change(within(builder).getByLabelText("Duration (hours)"), { target: { value: "8" } });
    fireEvent.click(screen.getByTestId("add-scenario"));
    expect(screen.getByTestId("scenario-list")).toHaveTextContent("MC-CNC5-01 down for 8 h");

    fireEvent.click(screen.getByTestId("run-simulation"));
    await waitFor(() => expect(api.find("POST", "/api/v1/schedule/simulate")).toHaveLength(1));
    expect(api.find("POST", "/api/v1/schedule/simulate")[0]?.body).toEqual({ scenarios: [{ kind: "machine_down", machine_id: "MC-CNC5-01", duration_hours: 8 }], top_n: 20 });

    expect(await screen.findByTestId("simulation-summary")).toHaveTextContent("If MC-CNC5-01 is down for 8 h, 37 orders are affected");
    const kpis = screen.getByTestId("simulation-kpis");
    expect(within(kpis).getByText("Late orders").parentElement).toHaveTextContent("2,540");
    expect(within(kpis).getByText("Late orders").parentElement).toHaveTextContent("was 2,521");
    expect(within(kpis).getByText("Orders affected").parentElement).toHaveTextContent("37");
    const table = screen.getByRole("table", { name: "Affected orders" });
    const row = table.querySelector('tr[data-rowkey="SO-1"]');
    expect(row).toHaveTextContent("newly late");
    expect(row).toHaveTextContent("MC-CNC5-01 → MC-CNC5-02");
    expect(row).toHaveTextContent("+24.0h");
    expect(screen.getByText("CNC 5-axis (new)")).toBeInTheDocument();
    expect(screen.getByText(/never changed/)).toBeInTheDocument();
  });

  it("saves the scenario set to localStorage for reuse", async () => {
    renderPage(<WhatIfSimulationPage />, { role: "planner", route: "/simulation" });
    const builder = await screen.findByTestId("scenario-builder");
    const machine = within(builder).getByLabelText("Machine");
    await waitFor(() => expect(within(machine).getAllByRole("option").length).toBeGreaterThan(1));
    fireEvent.change(machine, { target: { value: "MC-CNC5-01" } });
    fireEvent.change(within(builder).getByLabelText("Duration (hours)"), { target: { value: "4" } });
    fireEvent.click(screen.getByTestId("add-scenario"));
    fireEvent.change(screen.getByLabelText("Scenario set name"), { target: { value: "Spindle failure" } });
    fireEvent.click(screen.getByTestId("save-scenarios"));
    const saved = JSON.parse(localStorage.getItem(SAVED_SCENARIOS_KEY) ?? "[]") as Array<{ name: string; scenarios: unknown[] }>;
    expect(saved).toHaveLength(1);
    expect(saved[0]?.name).toBe("Spindle failure");
    expect(saved[0]?.scenarios).toEqual([{ kind: "machine_down", machine_id: "MC-CNC5-01", duration_hours: 4 }]);
    expect(screen.getByRole("button", { name: "Load" })).toBeInTheDocument();
  });

  it("builds every scenario kind with only the fields the planner set", () => {
    expect(buildScenario("machine_down", { machine_id: "M1", duration_hours: "8", reason: " " })).toEqual({ kind: "machine_down", machine_id: "M1", duration_hours: 8 });
    expect(buildScenario("machine_down", { machine_id: "M1" })).toBeNull();
    expect(buildScenario("extra_shift", { day: "2026-09-13", start: "18:00", end: "22:00" })).toEqual({ kind: "extra_shift", day: "2026-09-13", start: "18:00:00", end: "22:00:00" });
    expect(buildScenario("outsource", { order_ids: "SO-1, SO-2", quantity: "500" })).toEqual({ kind: "outsource", order_ids: ["SO-1", "SO-2"], quantity: 500 });
    expect(buildScenario("weight_change", { weights: "due_date_urgency=40, margin=15" })).toEqual({ kind: "weight_change", weights: { due_date_urgency: 40, margin: 15 } });
    expect(buildScenario("prioritize_customer", { customer_id: "C-1", boost_points: "20", tier_override: "strategic", label: "Board ask" })).toEqual({ kind: "prioritize_customer", customer_id: "C-1", boost_points: 20, tier_override: "strategic", label: "Board ask" });
    const urgent = buildScenario("urgent_orders", { mode: "clone", clone_of: "SO-9", due: "2026-09-13T10:00", count: "3" });
    expect(urgent?.kind === "urgent_orders" ? urgent.orders : []).toHaveLength(3);
    expect(buildScenario("expedite_orders", { order_ids: "SO-1", hours: "6" })).toEqual({ kind: "expedite_orders", order_ids: ["SO-1"], hours: 6 });
  });
});
