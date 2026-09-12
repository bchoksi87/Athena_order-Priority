import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SyncRunRequest } from "@/api/types";
import { authHandlers, mockApi, renderPage, type MockApi } from "@/test/utils";
import { capabilityReport, healthResponse, makeSyncRun, metricsResponse, pageOf, priorityConfiguration, syncStatus } from "@/test/apiFixtures";

import SystemAdministrationPage from "./SystemAdministrationPage";

describe("SystemAdministrationPage", () => {
  let api: MockApi;

  beforeEach(() => {
    localStorage.clear();
    api = mockApi({
      ...authHandlers("admin"),
      "GET /api/v1/health": healthResponse,
      "GET /api/v1/metrics": metricsResponse,
      "GET /api/v1/users": [{ user_id: "usr_admin", username: "admin", role: "admin", display_name: "System Administrator", email: null, active: true, last_login_at: "2026-09-12T13:00:00Z", created_at: "2026-09-01T00:00:00Z" }],
      "GET /api/v1/sync/status": syncStatus,
      "GET /api/v1/sync/runs": pageOf([makeSyncRun(), makeSyncRun({ run_id: "sync_0", mode: "incremental", status: "failed", error_message: "connector timeout", reconciliation: null })], 1, 20, 2),
      "GET /api/v1/sync/runs/sync_2": makeSyncRun({ run_id: "sync_2", mode: "incremental" }),
      "GET /api/v1/sync/capabilities": capabilityReport,
      "GET /api/v1/priority/configuration": priorityConfiguration,
      "GET /api/v1/scheduling/configuration": { version: null, scheduling: { config_id: "SchedulingConfig-A", name: "Default", version: 1, algorithm: "rule_based", horizon_days: 14 }, replanning: {}, alerts: {}, data_quality: {} },
      "POST /api/v1/sync/run": (req) => makeSyncRun({ run_id: "sync_2", mode: (req.body as SyncRunRequest).mode ?? "full", issues_count: 0 }),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows health from GET /health, the sync run history and a failed run as an exception", async () => {
    renderPage(<SystemAdministrationPage />, { role: "admin", route: "/admin" });
    const health = await screen.findByTestId("admin-health");
    await within(health).findByText("reachable");
    expect(within(health).getByText("v4")).toBeInTheDocument();
    // API and database both report "ok".
    expect(within(health).getAllByText("ok", { selector: ".kpi-value" })).toHaveLength(2);
    const runs = await screen.findByRole("table", { name: "Sync runs" });
    const rows = Array.from(runs.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(2);
    expect(within(runs).getByText("connector timeout")).toBeInTheDocument();
    expect(within(runs).getByText("connector timeout").closest("tr")).toHaveClass("row-late");
    // Fetched and upserted totals are the sum over all entities (both fixture runs report the same counts).
    expect(within(runs).getAllByText("28,761")).toHaveLength(4);
  });

  it("renders the Required / Available / Missing table with the missing required field highlighted", async () => {
    renderPage(<SystemAdministrationPage />, { role: "admin", route: "/admin" });
    await screen.findByRole("table", { name: "Sync runs" });
    fireEvent.click(screen.getByRole("tab", { name: /Field capabilities/ }));
    const panel = await screen.findByTestId("sync-capabilities");
    expect(within(panel).getByText("Missing required").parentElement).toHaveTextContent("1");
    expect(within(panel).getByText("Can schedule").parentElement).toHaveTextContent("no");
    const table = within(panel).getByRole("table", { name: "ERP field capabilities" });
    const missing = within(table).getByText("cycle_minutes_per_unit").closest("tr")!;
    expect(missing).toHaveClass("row-late");
    expect(within(missing).getByText("Missing")).toBeInTheDocument();
    expect(within(missing).getByText("Map the routing cycle time column")).toBeInTheDocument();
    fireEvent.change(within(panel).getByLabelText("Status"), { target: { value: "Missing" } });
    expect(within(table).queryByText("customer_id")).not.toBeInTheDocument();
  });

  it("runs a sync with the exact request body and opens the resulting run", async () => {
    renderPage(<SystemAdministrationPage />, { role: "admin", route: "/admin" });
    await screen.findByRole("table", { name: "Sync runs" });
    fireEvent.click(screen.getByRole("button", { name: "Run sync" }));
    const dialog = await screen.findByRole("dialog", { name: "Run ERP synchronisation" });
    fireEvent.change(within(dialog).getByLabelText(/Mode/), { target: { value: "incremental" } });
    fireEvent.click(within(dialog).getByTestId("sync-run-submit"));
    await waitFor(() => expect(api.find("POST", "/api/v1/sync/run")).toHaveLength(1));
    expect(api.find("POST", "/api/v1/sync/run")[0]!.body).toEqual({ mode: "incremental", prune_missing_orders: false });
    expect(await screen.findByText(/Incremental sync completed/)).toBeInTheDocument();
    const detail = await screen.findByTestId("sync-run-detail");
    expect(within(detail).getByText("connector 5021 vs stored 5101", { exact: false })).toBeInTheDocument();
  });
});
