import { describe, expect, it } from "vitest";

import { toCsv } from "./csv";

describe("toCsv", () => {
  it("quotes commas, quotes and newlines and blanks nulls", () => {
    const csv = toCsv(
      [
        { id: "A,1", name: 'He said "hi"', n: null },
        { id: "B", name: "multi\nline", n: 3 },
      ],
      [
        { header: "Id", value: (r) => r.id },
        { header: "Name", value: (r) => r.name },
        { header: "N", value: (r) => r.n },
      ],
    );
    expect(csv.split("\r\n")).toEqual(["Id,Name,N", '"A,1","He said ""hi""",', 'B,"multi\nline",3']);
  });
});
