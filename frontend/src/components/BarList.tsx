import type { ReactNode } from "react";

import type { Tone } from "@/lib/constants";
import { formatNumber } from "@/lib/formatters";

import "./BarList.css";

export interface BarListItem {
  key: string;
  label: ReactNode;
  value: number;
  tone?: Tone;
  hint?: string;
  onClick?: () => void;
}

export interface BarListProps {
  items: BarListItem[];
  /** Scale bars against this value instead of the maximum item. */
  max?: number;
  /** Show the share of `total` next to the value. */
  total?: number;
  emptyMessage?: string;
}

/** Horizontal bar list: label · bar · value (for breakdowns such as "orders blocked by reason"). */
export function BarList({ items, max, total, emptyMessage = "Nothing to show" }: BarListProps) {
  if (items.length === 0) return <div className="text-muted text-sm">{emptyMessage}</div>;
  const scale = max ?? Math.max(...items.map((i) => i.value), 1);
  return (
    <ul className="barlist" data-testid="barlist">
      {items.map((item) => {
        const pct = Math.max(0, Math.min(100, (item.value / scale) * 100));
        const content = (
          <>
            <span className="barlist-label" title={item.hint}>
              {item.label}
            </span>
            <span className="barlist-track">
              <span className={`barlist-fill tone-${item.tone ?? "running"}`} style={{ width: `${pct}%` }} />
            </span>
            <span className="barlist-value num">
              {formatNumber(item.value)}
              {total ? <span className="text-faint"> · {((100 * item.value) / Math.max(1, total)).toFixed(0)}%</span> : null}
            </span>
          </>
        );
        return (
          <li key={item.key} className="barlist-item">
            {item.onClick ? (
              <button type="button" className="barlist-btn" onClick={item.onClick}>
                {content}
              </button>
            ) : (
              content
            )}
          </li>
        );
      })}
    </ul>
  );
}
