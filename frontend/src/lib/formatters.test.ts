import { describe, expect, it } from "vitest";

import { formatCurrency, formatHours, formatMinutes, formatPct, formatSigned } from "./formatters";

describe("formatters", () => {
  it("formats INR with lakh/crore compaction", () => {
    expect(formatCurrency(4_260_000)).toBe("₹42.6 L");
    expect(formatCurrency(12_500_000)).toBe("₹1.3 Cr");
    expect(formatCurrency(12_500_000, { scale: "lakh" })).toBe("₹125.0 L");
    expect(formatCurrency(12_345)).toBe("₹12,345");
    expect(formatCurrency(null)).toBe("—");
  });

  it("formats durations", () => {
    expect(formatMinutes(135)).toBe("2h 15m");
    expect(formatMinutes(30)).toBe("30m");
    expect(formatMinutes(60 * 72)).toBe("3d 0h");
    expect(formatHours(2.4)).toBe("2.4h");
    expect(formatHours(96)).toBe("4.0d");
  });

  it("formats signed numbers and percentages", () => {
    expect(formatSigned(25)).toBe("+25");
    expect(formatSigned(-3)).toBe("-3");
    expect(formatSigned(0)).toBe("0");
    expect(formatPct(87.44)).toBe("87.4%");
  });
});
