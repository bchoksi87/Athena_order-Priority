import { useEffect, useRef, useState } from "react";

import "./ColumnPicker.css";

export interface ColumnPickerOption {
  key: string;
  label: string;
  /** Always shown; cannot be hidden. */
  locked?: boolean;
}

export interface ColumnPickerProps {
  columns: ColumnPickerOption[];
  hidden: string[];
  onChange: (hidden: string[]) => void;
  label?: string;
}

/** Dropdown of checkboxes to show/hide table columns. */
export function ColumnPicker({ columns, hidden, onChange, label = "Columns" }: ColumnPickerProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="colpick" ref={ref}>
      <button type="button" className="btn btn-sm" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {label}
        {hidden.length > 0 ? <span className="text-faint"> ({columns.length - hidden.length}/{columns.length})</span> : null}
      </button>
      {open ? (
        <div className="colpick-menu" role="menu">
          {columns.map((c) => {
            const on = !hidden.includes(c.key);
            return (
              <label key={c.key} className={`colpick-item${c.locked ? " locked" : ""}`}>
                <input
                  type="checkbox"
                  checked={on}
                  disabled={c.locked}
                  onChange={() => onChange(on ? [...hidden, c.key] : hidden.filter((k) => k !== c.key))}
                />
                <span>{c.label}</span>
              </label>
            );
          })}
          <div className="colpick-foot">
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange([])} disabled={hidden.length === 0}>
              Show all
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
