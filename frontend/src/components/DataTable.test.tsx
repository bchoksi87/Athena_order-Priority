import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DataTable, type Column } from "./DataTable";

interface Row {
  id: string;
  name: string;
  score: number;
}

const rows: Row[] = [
  { id: "a", name: "Alpha", score: 40 },
  { id: "b", name: "Bravo", score: 90 },
  { id: "c", name: "Charlie", score: 65 },
];

const columns: Column<Row>[] = [
  { key: "name", header: "Name", cell: (r) => r.name, sortValue: (r) => r.name, filterValue: (r) => r.name },
  { key: "score", header: "Score", cell: (r) => r.score, sortValue: (r) => r.score, numeric: true },
];

function bodyOrder(): string[] {
  const body = screen.getByTestId("datatable").querySelector("tbody");
  return Array.from(body?.querySelectorAll("tr") ?? []).map((tr) => tr.getAttribute("data-rowkey") ?? "");
}

describe("DataTable", () => {
  it("renders rows and sorts on header click", () => {
    render(<DataTable rows={rows} columns={columns} rowKey={(r) => r.id} />);
    expect(bodyOrder()).toEqual(["a", "b", "c"]);
    fireEvent.click(screen.getByText("Score"));
    expect(bodyOrder()).toEqual(["a", "c", "b"]);
    fireEvent.click(screen.getByText("Score"));
    expect(bodyOrder()).toEqual(["b", "c", "a"]);
  });

  it("filters rows with the column filter", () => {
    render(<DataTable rows={rows} columns={columns} rowKey={(r) => r.id} filters />);
    fireEvent.change(screen.getByLabelText("Filter name"), { target: { value: "br" } });
    expect(bodyOrder()).toEqual(["b"]);
    expect(screen.getByText("1 of 3 rows")).toBeInTheDocument();
  });

  it("paginates and reports row clicks", () => {
    const onRowClick = vi.fn();
    render(<DataTable rows={rows} columns={columns} rowKey={(r) => r.id} pageSize={2} onRowClick={onRowClick} />);
    expect(bodyOrder()).toEqual(["a", "b"]);
    fireEvent.click(screen.getByText("›"));
    expect(bodyOrder()).toEqual(["c"]);
    const table = screen.getByTestId("datatable");
    fireEvent.click(within(table).getByText("Charlie"));
    expect(onRowClick).toHaveBeenCalledWith(rows[2]);
  });

  it("shows the empty message", () => {
    render(<DataTable rows={[]} columns={columns} rowKey={(r) => r.id} emptyMessage="No orders" />);
    expect(screen.getByText("No orders")).toBeInTheDocument();
  });
});
