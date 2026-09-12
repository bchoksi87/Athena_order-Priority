import { useMemo } from "react";

import { diffJson, formatJsonValue, type DiffRow } from "@/lib/diff";

import "./JsonDiff.css";

export interface JsonDiffProps {
  before: unknown;
  after: unknown;
  /** Also list unchanged leaves (collapsed by default). */
  showUnchanged?: boolean;
  maxRows?: number;
}

function Cell({ value, kind, side }: { value: unknown; kind: DiffRow["kind"]; side: "before" | "after" }) {
  if (value === undefined) return <td className="jd-cell jd-empty">—</td>;
  const cls = kind === "unchanged" ? "" : side === "before" ? "jd-before" : "jd-after";
  const text = typeof value === "object" && value !== null ? JSON.stringify(value, null, 1) : formatJsonValue(value, 400);
  return (
    <td className={`jd-cell ${cls}`}>
      <code>{text}</code>
    </td>
  );
}

/** Previous → new value diff table for audit entries (object leaves compared by dotted path). */
export function JsonDiff({ before, after, showUnchanged = false, maxRows = 400 }: JsonDiffProps) {
  const rows = useMemo(() => diffJson(before, after, showUnchanged), [before, after, showUnchanged]);
  if (rows.length === 0) {
    return <div className="text-muted text-sm">No differences between previous and new value.</div>;
  }
  return (
    <div className="jd" data-testid="json-diff">
      <table>
        <thead>
          <tr>
            <th>Field</th>
            <th>Previous</th>
            <th>New</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, maxRows).map((r) => (
            <tr key={r.path} className={`jd-row jd-${r.kind}`} data-kind={r.kind}>
              <td className="jd-path">
                <code>{r.path}</code>
                <span className={`jd-tag jd-tag-${r.kind}`}>{r.kind}</span>
              </td>
              <Cell value={r.before} kind={r.kind} side="before" />
              <Cell value={r.after} kind={r.kind} side="after" />
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows ? <div className="text-faint text-xs">… {rows.length - maxRows} more</div> : null}
    </div>
  );
}
