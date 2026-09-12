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
  /** Controlled sort state (server-side sorting): the table stops sorting client-side and reports clicks. */
  sort?: { key: string; direction: SortDirection } | null;
  onSortChange?: (sort: { key: string; direction: SortDirection } | null) => void;
  /** Disable client-side paging (rows are already one server page). */
  paginate?: boolean;
  /** Keys of columns to hide (column picker). */
  hiddenColumns?: readonly string[];
  /** Hide the footer row entirely (when a Pager is rendered outside). */
  hideFooter?: boolean;
  /** Called with the row and the keyboard event when Enter/Space is pressed on a focused row. */
  keyboardNavigation?: boolean;
  /** Extra attributes for the table element (e.g. aria-label). */
  ariaLabel?: string;
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
  columns: allColumns,
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
  sort: controlledSort,
  onSortChange,
  paginate = true,
  hiddenColumns,
  hideFooter = false,
  keyboardNavigation = true,
  ariaLabel,
}: DataTableProps<T>) {
  const [localSort, setLocalSort] = useState<{ key: string; direction: SortDirection } | null>(initialSort ?? null);
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [page, setPage] = useState(0);
  const serverSort = onSortChange !== undefined;
  const sort = serverSort ? (controlledSort ?? null) : localSort;
  const columns = useMemo(
    () => (hiddenColumns && hiddenColumns.length > 0 ? allColumns.filter((c) => !hiddenColumns.includes(c.key)) : allColumns),
    [allColumns, hiddenColumns],
  );

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
    if (!sort || serverSort) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return filtered;
    const getter = col.sortValue;
    const dir = sort.direction === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => compare(getter(a), getter(b)) * dir);
  }, [filtered, sort, columns, serverSort]);

  const effectivePageSize = paginate ? pageSize : Math.max(1, sorted.length);
  const pageCount = Math.max(1, Math.ceil(sorted.length / effectivePageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = paginate ? sorted.slice(currentPage * effectivePageSize, (currentPage + 1) * effectivePageSize) : sorted;

  const nextSort = (prev: { key: string; direction: SortDirection } | null, col: Column<T>) => {
    if (!prev || prev.key !== col.key) return { key: col.key, direction: "asc" as const };
    if (prev.direction === "asc") return { key: col.key, direction: "desc" as const };
    return null;
  };

  const toggleSort = (col: Column<T>) => {
    if (!col.sortValue) return;
    if (serverSort) {
      onSortChange?.(nextSort(sort, col));
    } else {
      setLocalSort((prev) => nextSort(prev, col));
    }
    setPage(0);
  };

  const alignClass = (col: Column<T>) => (col.align ?? (col.numeric ? "right" : "left")) === "right" ? "num" : col.align === "center" ? "center" : "";

  return (
    <div className={`dt${dense ? " dt-dense" : ""}`} data-testid="datatable">
      <div className="dt-scroll" style={maxHeight ? { maxHeight } : undefined}>
        <table aria-label={ariaLabel}>
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
                    onKeyDown={
                      onRowClick && keyboardNavigation
                        ? (e) => {
                            if (e.target !== e.currentTarget) return;
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onRowClick(row);
                            } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                              e.preventDefault();
                              const sibling = e.key === "ArrowDown" ? e.currentTarget.nextElementSibling : e.currentTarget.previousElementSibling;
                              if (sibling instanceof HTMLElement) sibling.focus();
                            }
                          }
                        : undefined
                    }
                    tabIndex={onRowClick && keyboardNavigation ? 0 : undefined}
                    aria-selected={selectedKey === key ? true : undefined}
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
      {hideFooter ? null : (
      <div className="dt-footer">
        <span>
          {sorted.length === rows.length ? `${rows.length} rows` : `${sorted.length} of ${rows.length} rows`}
          {totalCount !== undefined && totalCount > rows.length ? ` (${totalCount} total on server)` : ""}
        </span>
        {footer}
        {paginate && pageCount > 1 ? (
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
      )}
    </div>
  );
}
