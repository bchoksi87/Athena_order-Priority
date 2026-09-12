import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makePriorityResult } from "@/test/fixtures";

import { ExplanationPanel } from "./ExplanationPanel";

describe("ExplanationPanel", () => {
  it("renders factors as '+points — name — reason' lines sorted by magnitude", () => {
    render(<ExplanationPanel result={makePriorityResult()} />);
    expect(screen.getByTestId("explanation-score")).toHaveTextContent("91");
    const list = screen.getByTestId("explanation-factors");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent("+24");
    expect(items[0]).toHaveTextContent("Due Date Urgency");
    expect(items[0]).toHaveTextContent("Due in 18 hours");
    expect(items[3]).toHaveTextContent("-3");
    expect(items[3]).toHaveTextContent("Changeover required");
  });

  it("renders adjustments and blockers", () => {
    render(
      <ExplanationPanel
        result={makePriorityResult({
          blocked: true,
          readiness: "waiting_material",
          blocking_reasons: ["Material AL-6061 short by 4 kg"],
        })}
      />,
    );
    const adj = screen.getByTestId("explanation-adjustments");
    expect(adj).toHaveTextContent("+6");
    expect(adj).toHaveTextContent("Age");
    expect(adj).toHaveTextContent("Waiting 8 days");
    expect(screen.getByText("Material AL-6061 short by 4 kg")).toBeInTheDocument();
    expect(screen.getByText(/Blocked — Waiting material/)).toBeInTheDocument();
    expect(screen.getByTestId("explanation-total")).toHaveTextContent("91");
  });
});
