import type { PageMeta } from "@/api/types";

import "./Pager.css";

export interface PagerProps {
  meta: PageMeta | undefined;
  page: number;
  onPageChange: (page: number) => void;
  pageSize?: number;
  onPageSizeChange?: (size: number) => void;
  pageSizes?: number[];
  /** Number of rows currently shown (for "1–50 of 277"). */
  shown?: number;
  busy?: boolean;
}

/** Server-side pagination controls for page/page_size endpoints. */
export function Pager({ meta, page, onPageChange, pageSize, onPageSizeChange, pageSizes = [25, 50, 100, 200], shown, busy = false }: PagerProps) {
  const total = meta?.total ?? 0;
  const size = pageSize ?? meta?.limit ?? 50;
  const pages = meta?.pages ?? Math.max(1, Math.ceil(total / Math.max(1, size)));
  const first = total === 0 ? 0 : (page - 1) * size + 1;
  const last = total === 0 ? 0 : Math.min(total, first + (shown ?? size) - 1);
  return (
    <div className="pager" data-testid="pager">
      <span className="pager-range num">
        {first}–{last} of {total}
        {busy ? " · updating…" : ""}
      </span>
      {onPageSizeChange ? (
        <label className="pager-size">
          <span>Rows</span>
          <select className="select" value={size} onChange={(e) => onPageSizeChange(Number(e.target.value))} aria-label="Rows per page">
            {pageSizes.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <div className="pager-buttons">
        <button type="button" className="btn btn-sm" onClick={() => onPageChange(1)} disabled={page <= 1} aria-label="First page">
          «
        </button>
        <button type="button" className="btn btn-sm" onClick={() => onPageChange(page - 1)} disabled={page <= 1} aria-label="Previous page">
          ‹
        </button>
        <span className="num">
          {page} / {pages}
        </span>
        <button type="button" className="btn btn-sm" onClick={() => onPageChange(page + 1)} disabled={page >= pages} aria-label="Next page">
          ›
        </button>
        <button type="button" className="btn btn-sm" onClick={() => onPageChange(pages)} disabled={page >= pages} aria-label="Last page">
          »
        </button>
      </div>
    </div>
  );
}
