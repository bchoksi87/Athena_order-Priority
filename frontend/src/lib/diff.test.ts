import { describe, expect, it } from "vitest";

import { diffJson, formatJsonValue } from "./diff";

describe("diffJson", () => {
  it("reports added, removed and changed leaves with dotted paths", () => {
    const rows = diffJson({ a: 1, nested: { x: 1, y: [1, 2] }, gone: true }, { a: 2, nested: { x: 1, y: [1, 3] }, added: "n" });
    expect(rows).toEqual([
      { path: "a", kind: "changed", before: 1, after: 2 },
      { path: "added", kind: "added", before: undefined, after: "n" },
      { path: "gone", kind: "removed", before: true, after: undefined },
      { path: "nested.y", kind: "changed", before: [1, 2], after: [1, 3] },
    ]);
  });

  it("compares primitives and nulls as whole values", () => {
    expect(diffJson(null, { score: 80 })).toEqual([{ path: "value", kind: "changed", before: null, after: { score: 80 } }]);
    expect(diffJson("a", "a")).toEqual([]);
    expect(diffJson("a", "a", true)).toEqual([{ path: "value", kind: "unchanged", before: "a", after: "a" }]);
  });

  it("formats values compactly", () => {
    expect(formatJsonValue(undefined)).toBe("—");
    expect(formatJsonValue(null)).toBe("null");
    expect(formatJsonValue({ a: 1 })).toBe('{"a":1}');
    expect(formatJsonValue("x".repeat(100), 10)).toHaveLength(10);
  });
});
