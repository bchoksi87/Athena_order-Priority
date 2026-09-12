import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { explanationLines } from "@/test/apiFixtures";

import { formatPoints } from "@/lib/formatters";

import { ExplanationLines } from "./ExplanationLines";

describe("ExplanationLines", () => {
  it("formats points like the backend renderer", () => {
    expect(formatPoints(28)).toBe("+28");
    expect(formatPoints(-3)).toBe("-3");
    expect(formatPoints(27.4)).toBe("+27.4");
    expect(formatPoints(0.01)).toBe("0");
  });

  it("groups bonuses, penalties, adjustments and caps and keeps the API text verbatim", () => {
    render(<ExplanationLines lines={[...explanationLines, { kind: "cap", key: "clamp", label: "Cap", points: -1.2, reason: "Score limited to 0..100 (raw total 101.2)" }]} score={100} />);
    expect(within(screen.getByTestId("explanation-bonus-lines")).getAllByRole("listitem")).toHaveLength(6);
    expect(within(screen.getByTestId("explanation-penalty-lines")).getAllByRole("listitem")).toHaveLength(1);
    expect(within(screen.getByTestId("explanation-adjustment-lines")).getAllByRole("listitem")).toHaveLength(1);
    const cap = within(screen.getByTestId("explanation-cap-lines")).getAllByRole("listitem")[0];
    expect(cap).toHaveTextContent("-1.2");
    expect(cap).toHaveTextContent("Cap: Score limited to 0..100 (raw total 101.2)");
    expect(screen.getByTestId("explanation-lines-total")).toHaveTextContent("100");
  });

  it("renders an empty message without inventing lines", () => {
    render(<ExplanationLines lines={[]} score={0} />);
    expect(screen.getByText(/No explanation lines/)).toBeInTheDocument();
  });
});
