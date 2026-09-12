import type { ReactNode } from "react";

import "./Layout.css";

export interface FilterOption {
  value: string;
  label: string;
}

export interface FilterField {
  key: string;
  label: string;
  kind: "select" | "text";
  options?: FilterOption[];
  placeholder?: string;
}

export interface FilterBarProps {
  fields: FilterField[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onReset?: () => void;
  search?: { value: string; onChange: (v: string) => void; placeholder?: string };
  children?: ReactNode;
}

/** Horizontal strip of select/text filters with a search box and reset. */
export function FilterBar({ fields, values, onChange, onReset, search, children }: FilterBarProps) {
  const active = Object.values(values).some((v) => v !== "") || Boolean(search?.value);
  return (
    <div className="filterbar" role="group" aria-label="Filters">
      {search ? (
        <label className="field filterbar-search">
          <span className="label">Search</span>
          <input
            className="input"
            value={search.value}
            placeholder={search.placeholder ?? "Search…"}
            onChange={(e) => search.onChange(e.target.value)}
          />
        </label>
      ) : null}
      {fields.map((f) => (
        <label className="field" key={f.key}>
          <span className="label">{f.label}</span>
          {f.kind === "select" ? (
            <select className="select" value={values[f.key] ?? ""} onChange={(e) => onChange(f.key, e.target.value)}>
              <option value="">All</option>
              {(f.options ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="input"
              value={values[f.key] ?? ""}
              placeholder={f.placeholder}
              onChange={(e) => onChange(f.key, e.target.value)}
            />
          )}
        </label>
      ))}
      <div className="filterbar-actions">
        {children}
        {onReset ? (
          <button type="button" className="btn btn-sm btn-ghost" onClick={onReset} disabled={!active}>
            Reset
          </button>
        ) : null}
      </div>
    </div>
  );
}
