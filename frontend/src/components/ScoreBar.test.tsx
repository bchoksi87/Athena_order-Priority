import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScoreBar } from "./ScoreBar";

describe("ScoreBar", () => {
  it("renders the clamped value and a tone-coloured fill", () => {
    render(<ScoreBar value={91.4} />);
    expect(screen.getByText("91")).toBeInTheDocument();
    const fill = screen.getByTestId("scorebar-fill");
    expect(fill).toHaveStyle({ width: "91.4%" });
    expect(fill.className).toContain("tone-late");
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "91.4");
  });

  it("clamps out-of-range values and renders a dash for null", () => {
    const { rerender } = render(<ScoreBar value={140} />);
    expect(screen.getByTestId("scorebar-fill")).toHaveStyle({ width: "100%" });
    rerender(<ScoreBar value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows the breakdown tooltip on hover", () => {
    render(<ScoreBar value={60} breakdown={[{ label: "Due date", points: 25 }, { label: "Setup", points: -3 }]} />);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByTestId("scorebar"));
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Due date");
    expect(tip).toHaveTextContent("+25");
    expect(tip).toHaveTextContent("-3");
  });
});
