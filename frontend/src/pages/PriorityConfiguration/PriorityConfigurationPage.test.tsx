import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PreviewRequest } from "@/api/types";
import { authHandlers, mockApi, renderPage, type MockApi } from "@/test/utils";
import { configVersion, previewResponse, priorityConfiguration } from "@/test/apiFixtures";

import { normaliseWeights } from "@/lib/weights";

import PriorityConfigurationPage from "./PriorityConfigurationPage";

describe("normaliseWeights", () => {
  it("normalises enabled weights to 100 and zeroes disabled ones", () => {
    const shares = normaliseWeights([
      { key: "due_date_urgency", weight: 30, enabled: true, params: {} },
      { key: "sla_risk", weight: 10, enabled: true, params: {} },
      { key: "margin", weight: 50, enabled: false, params: {} },
    ]);
    expect(shares.due_date_urgency).toBeCloseTo(75);
    expect(shares.sla_risk).toBeCloseTo(25);
    expect(shares.margin).toBe(0);
  });
});

describe("PriorityConfigurationPage", () => {
  let api: MockApi;

  beforeEach(() => {
    localStorage.clear();
    api = mockApi({
      ...authHandlers("admin"),
      "GET /api/v1/priority/configuration": priorityConfiguration,
      "GET /api/v1/priority/configuration/versions": [configVersion],
      "POST /api/v1/priority/configuration/preview": previewResponse,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows normalised shares and updates them live when a weight changes", async () => {
    renderPage(<PriorityConfigurationPage />, { role: "admin", route: "/config/priority" });
    await screen.findByTestId("weights-table");
    expect(screen.getByTestId("weight-share-due_date_urgency")).toHaveTextContent("25.0%");
    expect(screen.getByTestId("weight-share-margin")).toHaveTextContent("—");
    fireEvent.change(screen.getByLabelText("Weight value due_date_urgency"), { target: { value: "75" } });
    // 75 / (75 + 25 + 25 + 25) = 50%
    expect(screen.getByTestId("weight-share-due_date_urgency")).toHaveTextContent("50.0%");
    expect(screen.getByTestId("weight-share-sla_risk")).toHaveTextContent("16.7%");
    expect(screen.getByText(/1 UNSAVED CHANGE/)).toBeInTheDocument();
  });

  it("calls the preview endpoint with the candidate profile and renders the summary", async () => {
    renderPage(<PriorityConfigurationPage />, { role: "admin", route: "/config/priority" });
    await screen.findByTestId("weights-table");
    fireEvent.change(screen.getByLabelText("Weight value due_date_urgency"), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview impact" }));
    await waitFor(() => expect(api.find("POST", "/api/v1/priority/configuration/preview")).toHaveLength(1));
    const body = api.find("POST", "/api/v1/priority/configuration/preview")[0]!.body as PreviewRequest;
    expect(body.top_n).toBe(50);
    expect(body.profile.weights.find((w) => w.key === "due_date_urgency")?.weight).toBe(40);
    expect(body.profile.profile_id).toBe("PriorityProfile-A");
    expect(await screen.findByText(previewResponse.summary)).toBeInTheDocument();
    expect(screen.getAllByText("SO2609-00010-01").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("▲ 12")).toBeInTheDocument();
  });
});
