import { useMemo, useState, type ReactNode } from "react";

import "./DataTable.css";

export type SortDirection = "asc" | "desc";
export type SortValue = string | number | null | undefined;

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Renders the cell. */
  cell: (row: T) => ReactNode;
  /** Value used for sorting; omit to make the column unsortable. */
  sortValue?: (row: T) => SortValue;
  /** Value used for the column text filter; omit to disable filtering for this column. */
  filterValue?: (row: T) => string;
  align?: "left" | "right" | "center";
  width?: number | string;
  /** Renders with the monospace numeric style (implies right alignment unless overridden). */
  numeric?: boolean;
  title?: string;
}

export interface DataTableProps<T> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  /** Row modifier class (e.g. "row-late") to highlight exceptions. */
  rowClassName?: (row: T) => string | undefined;
  pageSize?: number;
  dense?: boolean;
  /** Show the per-column filter row. */
  filters?: boolean;
  initialSort?: { key: string; direction: SortDirection };
  emptyMessage?: string;
  maxHeight?: number | string;
  /** Server-side total, when the rows are one page of a larger set. */
  totalCount?: number;
  footer?: ReactNode;
}

function compare(a: SortValue, b: SortValue): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

/**
 * Generic dense data table with client-side sorting, per-column text filters,
 * sticky header, pagination and exception row highlighting.
 */
export function DataTable<T>({
  rows,
  columns,
  rowKey,
  onRowClick,
  selectedKey,
  rowClassName,
  pageSize = 50,
  dense = false,
  filters = false,
  initialSort,
  emptyMessage = "No rows",
  maxHeight,
  totalCount,
  footer,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; direction: SortDirection } | null>(initialSort ?? null);
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    const active = Object.entries(filterValues).filter(([, v]) => v.trim() !== "");
    if (active.length === 0) return rows;
    return rows.filter((row) =>
      active.every(([key, needle]) => {
        const col = columns.find((c) => c.key === key);
        if (!col?.filterValue) return true;
        return col.filterValue(row).toLowerCase().includes(needle.trim().toLowerCase());
      }),
    );
  }, [rows, columns, filterValues]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return filtered;
    const getter = col.sortValue;
    const dir = sort.direction === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => compare(getter(a), getter(b)) * dir);
  }, [filtered, sort, columns]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = sorted.slice(currentPage * pageSize, (currentPage + 1) * pageSize);

  const toggleSort = (col: Column<T>) => {
    if (!col.sortValue) return;
    setSort((prev) => {
      if (!prev || prev.key !== col.key) return { key: col.key, direction: "asc" };
      if (prev.direction === "asc") return { key: col.key, direction: "desc" };
      return null;
    });
    setPage(0);
  };

  const alignClass = (col: Column<T>) => (col.align ?? (col.numeric ? "right" : "left")) === "right" ? "num" : col.align === "center" ? "center" : "";

  return (
    <div className={`dt${dense ? " dt-dense" : ""}`} data-testid="datatable">
      <div className="dt-scroll" style={maxHeight ? { maxHeight } : undefined}>
        <table>
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={`${alignClass(col)}${col.sortValue ? " sortable" : ""}`}
                  style={col.width ? { width: col.width } : undefined}
                  onClick={() => toggleSort(col)}
                  title={col.title}
                  aria-sort={sort?.key === col.key ? (sort.direction === "asc" ? "ascending" : "descending") : undefined}
                >
                  {col.header}
                  {sort?.key === col.key ? <span className="dt-sort-icon">{sort.direction === "asc" ? "▲" : "▼"}</span> : null}
                </th>
              ))}
            </tr>
            {filters ? (
              <tr className="dt-filters">
                {columns.map((col) => (
                  <th key={col.key}>
                    {col.filterValue ? (
                      <input
                        className="dt-filter-input"
                        value={filterValues[col.key] ?? ""}
                        placeholder="filter"
                        aria-label={`Filter ${col.key}`}
                        onChange={(e) => {
                          setFilterValues((prev) => ({ ...prev, [col.key]: e.target.value }));
                          setPage(0);
                        }}
                      />
                    ) : null}
                  </th>
                ))}
              </tr>
            ) : null}
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>
                  <div className="dt-empty">{emptyMessage}</div>
                </td>
              </tr>
            ) : (
              visible.map((row) => {
                const key = rowKey(row);
                const cls = [onRowClick ? "clickable" : "", selectedKey === key ? "selected" : "", rowClassName?.(row) ?? ""]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <tr
                    key={key}
                    className={cls || undefined}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    data-rowkey={key}
                  >
                    {columns.map((col) => (
                      <td key={col.key} className={alignClass(col) || undefined}>
                        {col.cell(row)}
                      </td>
                    ))}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <div className="dt-footer">
        <span>
          {sorted.length === rows.length ? `${rows.length} rows` : `${sorted.length} of ${rows.length} rows`}
          {totalCount !== undefined && totalCount > rows.length ? ` (${totalCount} total on server)` : ""}
        </span>
        {footer}
        {pageCount > 1 ? (
          <div className="dt-pager">
            <button type="button" className="btn btn-sm" onClick={() => setPage(0)} disabled={currentPage === 0}>
              «
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={currentPage === 0}>
              ‹
            </button>
            <span className="num">
              {currentPage + 1} / {pageCount}
            </span>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={currentPage >= pageCount - 1}
            >
              ›
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setPage(pageCount - 1)} disabled={currentPage >= pageCount - 1}>
              »
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
