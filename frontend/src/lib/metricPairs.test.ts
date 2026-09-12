import { describe, expect, it } from "vitest";

import { formatMetricDelta, formatMetricValue, formatPairTransition, higherIsBetter, metricImproved, orderedMetricPairs } from "./metricPairs";

describe("metricPairs", () => {
  it("formats the spec transitions per unit", () => {
    expect(formatPairTransition("on_time_pct", { label: "On-time delivery", before: 87, after: 94, delta: 7 })).toBe("87% → 94%");
    expect(formatPairTransition("avg_lateness_hours", { label: "Average lateness", before: 8.2, after: 2.4, delta: -5.8 })).toBe("8.2h → 2.4h");
    expect(formatPairTransition("total_setup_hours", { label: "Setup hours", before: 126, after: 101, delta: -25 })).toBe("126.0h → 101.0h");
    expect(formatMetricValue("revenue_at_risk", 8_535_733)).toBe("₹85.4 L");
    expect(formatMetricDelta("on_time_pct", 7)).toBe("+7.0 pt");
  });

  it("knows the direction of every headline metric", () => {
    expect(higherIsBetter("on_time_pct")).toBe(true);
    expect(higherIsBetter("avg_lateness_hours")).toBe(false);
    expect(metricImproved("late_orders", { label: "Late", before: 131, after: 20, delta: -111 })).toBe(true);
    expect(metricImproved("overall_utilization_pct", { label: "Util", before: 76, after: 87, delta: 11 })).toBe(true);
    expect(metricImproved("total_setup_hours", { label: "Setup", before: 100, after: 100, delta: 0 })).toBeUndefined();
  });

  it("orders pairs by the spec headline then alphabetically", () => {
    const keys = orderedMetricPairs({
      zzz: { label: "z", before: 1, after: 1, delta: 0 },
      total_setup_hours: { label: "s", before: 1, after: 1, delta: 0 },
      on_time_pct: { label: "o", before: 1, after: 1, delta: 0 },
    }).map(([k]) => k);
    expect(keys).toEqual(["on_time_pct", "total_setup_hours", "zzz"]);
  });
});
