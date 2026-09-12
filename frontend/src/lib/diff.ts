/**
 * Structural diff of two JSON values for the audit log ("previous → new").
 * Objects are compared key by key (recursively, with dotted paths); arrays and
 * primitives are compared as whole values.
 */

export type DiffKind = "added" | "removed" | "changed" | "unchanged";

export interface DiffRow {
  path: string;
  kind: DiffKind;
  before: unknown;
  after: unknown;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => deepEqual(v, b[i]));
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) if (!deepEqual(a[k], b[k])) return false;
    return true;
  }
  return false;
}

/** Flattens the differences between `before` and `after` into rows; `includeUnchanged` keeps equal leaves. */
export function diffJson(before: unknown, after: unknown, includeUnchanged = false, prefix = ""): DiffRow[] {
  const rows: DiffRow[] = [];
  if (isPlainObject(before) && isPlainObject(after)) {
    const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
    for (const key of keys) {
      const path = prefix ? `${prefix}.${key}` : key;
      const inBefore = key in before;
      const inAfter = key in after;
      if (inBefore && !inAfter) rows.push({ path, kind: "removed", before: before[key], after: undefined });
      else if (!inBefore && inAfter) rows.push({ path, kind: "added", before: undefined, after: after[key] });
      else rows.push(...diffJson(before[key], after[key], includeUnchanged, path));
    }
    return rows;
  }
  const path = prefix || "value";
  if (before === undefined && after !== undefined) return [{ path, kind: "added", before, after }];
  if (before !== undefined && after === undefined) return [{ path, kind: "removed", before, after }];
  if (deepEqual(before, after)) return includeUnchanged ? [{ path, kind: "unchanged", before, after }] : [];
  return [{ path, kind: "changed", before, after }];
}

/** Compact one-line rendering of any JSON value for table cells. */
export function formatJsonValue(value: unknown, max = 80): string {
  if (value === undefined) return "—";
  if (value === null) return "null";
  let text: string;
  if (typeof value === "string") text = value;
  else {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
