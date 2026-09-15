/** Small helpers shared by the demo backend: ids, dates, pagination, JSON diffs, query parsing. */
import type { PageResponse } from "@/api/types";

import { invalidField, validation } from "./errors";

// ------------------------------------------------------------------ ids

const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"; // Crockford base32, like app/core/ids.py

function encode(value: number, length: number): string {
  const chars: string[] = [];
  let v = value;
  for (let i = 0; i < length; i += 1) {
    chars.push(ALPHABET[v % 32] ?? "0");
    v = Math.floor(v / 32);
  }
  return chars.reverse().join("");
}

let lastIdTime = -1;
let lastIdCounter = 0;

/**
 * Time-sortable 26-char id with the backend's prefixes (`ovr_…`, `exp_…`, `lock_…`). Ids minted
 * in the same millisecond stay monotonic (a counter in the random part) so "newest first"
 * orderings that tie-break on the id (audit rows) are deterministic.
 */
export function newId(prefix: string, now: Date = new Date()): string {
  const millis = now.getTime();
  if (millis === lastIdTime) lastIdCounter += 1;
  else {
    lastIdTime = millis;
    lastIdCounter = 0;
  }
  const ts = encode(millis, 10);
  let rand = "";
  for (let i = 0; i < 12; i += 1) rand += ALPHABET[Math.floor(Math.random() * 32)];
  return `${prefix}_${ts}${encode(lastIdCounter % 1_048_576, 4)}${rand}`;
}

// ---------------------------------------------------------------- dates

/** ISO-8601 UTC like pydantic: `Z` suffix, no fractional part when it is zero. */
export function iso(d: Date): string {
  return d.toISOString().replace(/\.000Z$/, "Z");
}

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function hoursBetween(a: Date, b: Date): number {
  return (b.getTime() - a.getTime()) / 3_600_000;
}

export function addHours(d: Date, hours: number): Date {
  return new Date(d.getTime() + hours * 3_600_000);
}

export function addDays(d: Date, days: number): Date {
  return new Date(d.getTime() + days * 86_400_000);
}

/** Human wording for a duration in hours (factors/common.py describe_hours). */
export function describeHours(hours: number): string {
  const h = Math.abs(hours);
  if (h < 1) return `${Math.round(h * 60)} minutes`;
  if (h < 48) return `${Math.round(h)} hours`;
  return `${(h / 24).toFixed(1)} days`;
}

/** Python's `f"{x:g}"` for the numbers used in messages (drops trailing zeros). */
export function fmtG(value: number): string {
  if (Number.isInteger(value)) return String(value);
  return String(Number(value.toPrecision(6)));
}

// ----------------------------------------------------------- pagination

export function paginate<T>(items: T[], page: number, pageSize: number): PageResponse<T> {
  const start = (page - 1) * pageSize;
  const total = items.length;
  return {
    items: items.slice(start, start + pageSize),
    total,
    page,
    page_size: pageSize,
    pages: pageSize ? Math.ceil(total / pageSize) : 0,
    has_more: page * pageSize < total,
  };
}

// ------------------------------------------------------------ deep copy

export function clone<T>(value: T): T {
  return value === undefined ? value : (JSON.parse(JSON.stringify(value)) as T);
}

// ------------------------------------------------------------- json diff

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function flattenInto(value: unknown, prefix: string, out: Record<string, unknown>): void {
  if (isPlainObject(value)) {
    if (Object.keys(value).length === 0) out[prefix] = {};
    for (const [key, item] of Object.entries(value)) flattenInto(item, prefix ? `${prefix}.${key}` : key, out);
  } else if (Array.isArray(value)) {
    if (value.length === 0) out[prefix] = [];
    value.forEach((item, index) => flattenInto(item, `${prefix}[${index}]`, out));
  } else {
    out[prefix] = value;
  }
}

/** `{"a.b[0].c": leaf}` view of a JSON document (audit_service.flatten_json). */
export function flattenJson(value: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  flattenInto(value, "", out);
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

/** Only the leaves that differ: `[previous_changed, new_changed]` keyed by dotted path (audit_service.json_diff). */
export function jsonDiff(previous: unknown, next: unknown): [Record<string, unknown>, Record<string, unknown>] {
  const before = flattenJson(previous);
  const after = flattenJson(next);
  const changedPrevious: Record<string, unknown> = {};
  const changedNew: Record<string, unknown> = {};
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
  for (const key of keys) {
    const old = before[key];
    const cur = after[key];
    const inBefore = key in before;
    const inAfter = key in after;
    if (old !== cur || inBefore !== inAfter) {
      changedPrevious[key] = inBefore ? old : null;
      changedNew[key] = inAfter ? cur : null;
    }
  }
  return [changedPrevious, changedNew];
}

export function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ----------------------------------------------------------- query params

/** Typed access to the query string with FastAPI-like 422s on bad values. */
export class Query {
  constructor(private readonly params: URLSearchParams) {}

  raw(name: string): string | null {
    const v = this.params.get(name);
    return v === null || v === "" ? null : v;
  }

  str(name: string): string | undefined {
    return this.raw(name) ?? undefined;
  }

  list(name: string): string[] {
    return this.params.getAll(name).filter((v) => v !== "");
  }

  int(name: string, fallback: number, opts: { min?: number; max?: number } = {}): number {
    const raw = this.raw(name);
    if (raw === null) return fallback;
    const n = Number(raw);
    if (!Number.isInteger(n)) throw invalidField("query", name, "Input should be a valid integer");
    if (opts.min !== undefined && n < opts.min) throw invalidField("query", name, `Input should be greater than or equal to ${opts.min}`);
    if (opts.max !== undefined && n > opts.max) throw invalidField("query", name, `Input should be less than or equal to ${opts.max}`);
    return n;
  }

  optInt(name: string, opts: { min?: number; max?: number } = {}): number | undefined {
    return this.raw(name) === null ? undefined : this.int(name, 0, opts);
  }

  bool(name: string): boolean | undefined {
    const raw = this.raw(name);
    if (raw === null) return undefined;
    const v = raw.toLowerCase();
    if (["true", "1", "yes", "on"].includes(v)) return true;
    if (["false", "0", "no", "off"].includes(v)) return false;
    throw invalidField("query", name, "Input should be a valid boolean");
  }

  date(name: string): Date | undefined {
    const raw = this.raw(name);
    if (raw === null) return undefined;
    const d = parseDate(raw);
    if (!d) throw invalidField("query", name, "Input should be a valid datetime");
    return d;
  }

  oneOf<T extends string>(name: string, values: readonly T[]): T | undefined {
    const raw = this.raw(name);
    if (raw === null) return undefined;
    if (!(values as readonly string[]).includes(raw)) throw invalidField("query", name, `Input should be one of: ${values.join(", ")}`);
    return raw as T;
  }
}

// ---------------------------------------------------------------- bodies

export function bodyObject(body: unknown): Record<string, unknown> {
  if (body === null || body === undefined) return {};
  if (typeof body !== "object" || Array.isArray(body)) throw validation("request body must be a JSON object");
  return body as Record<string, unknown>;
}

/** Mandatory, non-blank reason of every mutating request (services/base.py require_reason + ReasonBody). */
export function requireReason(body: Record<string, unknown>, field = "reason"): string {
  const raw = body[field];
  if (raw === undefined || raw === null) throw invalidField("body", field, "Field required");
  if (typeof raw !== "string") throw invalidField("body", field, "Input should be a valid string");
  const text = raw.trim();
  if (!text) throw invalidField("body", field, "String should match pattern '\\S'");
  if (text.length > 2000) throw validation("reason must be at most 2000 characters", { field });
  return text;
}

export function optionalString(body: Record<string, unknown>, field: string): string | null {
  const raw = body[field];
  if (raw === undefined || raw === null) return null;
  if (typeof raw !== "string") throw invalidField("body", field, "Input should be a valid string");
  return raw;
}

export function optionalNumber(body: Record<string, unknown>, field: string): number | null {
  const raw = body[field];
  if (raw === undefined || raw === null) return null;
  if (typeof raw !== "number" || !Number.isFinite(raw)) throw invalidField("body", field, "Input should be a valid number");
  return raw;
}

export function optionalDate(body: Record<string, unknown>, field: string): Date | null {
  const raw = body[field];
  if (raw === undefined || raw === null || raw === "") return null;
  if (typeof raw !== "string") throw invalidField("body", field, "Input should be a valid datetime");
  const d = parseDate(raw);
  if (!d) throw invalidField("body", field, "Input should be a valid datetime");
  return d;
}

export function requiredString(body: Record<string, unknown>, field: string): string {
  const raw = body[field];
  if (raw === undefined || raw === null) throw invalidField("body", field, "Field required");
  if (typeof raw !== "string" || raw.length === 0) throw invalidField("body", field, "String should have at least 1 character");
  return raw;
}

/** Normalise an optional expiry and reject instants that are not after `now` (services/base.py ensure_future). */
export function ensureFuture(value: Date | null, now: Date, field: string): Date | null {
  if (value === null) return null;
  if (value.getTime() <= now.getTime()) throw validation(`${field} must be in the future`, { [field]: iso(value), now: iso(now) });
  return value;
}

export function overlaps(aStart: Date, aEnd: Date, bStart: Date, bEnd: Date): boolean {
  return aStart < bEnd && aEnd > bStart;
}

export function sortBy<T>(items: T[], key: (item: T) => Array<string | number | boolean | null>, reverse = false): T[] {
  const decorated = items.map((item, index) => ({ item, index, key: key(item) }));
  decorated.sort((a, b) => {
    for (let i = 0; i < Math.max(a.key.length, b.key.length); i += 1) {
      const x = a.key[i] ?? null;
      const y = b.key[i] ?? null;
      if (x === y) continue;
      if (x === null) return 1;
      if (y === null) return -1;
      if (x < y) return reverse ? 1 : -1;
      if (x > y) return reverse ? -1 : 1;
    }
    return a.index - b.index;
  });
  return decorated.map((d) => d.item);
}
