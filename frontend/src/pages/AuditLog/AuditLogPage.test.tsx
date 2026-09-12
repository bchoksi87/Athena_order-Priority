import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHandlers, mockApi, renderPage, type MockApi } from "@/test/utils";
import { makeAuditEntry, pageOf } from "@/test/apiFixtures";

import AuditLogPage from "./AuditLogPage";

describe("AuditLogPage", () => {
  let api: MockApi;

  beforeEach(() => {
    localStorage.clear();
    api = mockApi({
      ...authHandlers("production_manager"),
      "GET /api/v1/audit": pageOf([makeAuditEntry(), makeAuditEntry({ audit_id: "aud_2", action: "hold", previous_value: null, new_value: { on_hold: true }, reason: null })]),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes the deep-link filters to the API and renders previous → new diffs", async () => {
    renderPage(<AuditLogPage />, { role: "production_manager", route: "/audit?entity_id=SO2609-00093-02&entity_type=order" });
    const table = await screen.findByRole("table", { name: "Audit log" });
    const req = api.find("GET", "/api/v1/audit")[0]!;
    expect(req.url.searchParams.get("entity_id")).toBe("SO2609-00093-02");
    expect(req.url.searchParams.get("entity_type")).toBe("order");
    expect(req.url.searchParams.get("page")).toBe("1");
    expect(within(table).getByText("Customer escalation — line down")).toBeInTheDocument();
    // Inline summary shows the changed leaves.
    const row = within(table).getByText("Customer escalation — line down").closest("tr")!;
    expect(row).toHaveTextContent("expedite null → {\"boost_points\":30");
    expect(row).toHaveTextContent("score 72 → 91");

    fireEvent.click(row);
    const detail = await screen.findByTestId("audit-entry-detail");
    const diff = within(detail).getByTestId("json-diff");
    const rows = Array.from(diff.querySelectorAll("tbody tr"));
    const paths = rows.map((r) => r.querySelector(".jd-path code")?.textContent);
    expect(paths).toEqual(["expedite", "score"]);
    expect(rows[0]).toHaveAttribute("data-kind", "changed");
    expect(rows[0]).toHaveTextContent("boost_points");
    expect(rows[1]).toHaveAttribute("data-kind", "changed");
    expect(rows[1]).toHaveTextContent("72");
    expect(rows[1]).toHaveTextContent("91");
  });
});
