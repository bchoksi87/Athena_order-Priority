/**
 * Time helpers. The backend speaks timezone-aware UTC ISO strings; the UI
 * displays in the browser's local zone. Working-day arithmetic is the
 * backend's responsibility (calendar engine) — nothing here models shifts.
 */
import {
  addHours as dfAddHours,
  differenceInMinutes,
  format,
  formatDistanceStrict,
  isValid,
  parseISO,
  startOfDay,
} from "date-fns";

import { DASH } from "./formatters";

/** Parses an ISO-8601 string (UTC) into a Date; returns null when missing/invalid. */
export function parseUtc(value: string | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const d = value instanceof Date ? value : parseISO(value);
  return isValid(d) ? d : null;
}

/** Serialises a Date as an ISO string in UTC (what the API expects). */
export function toUtcIso(d: Date): string {
  return d.toISOString();
}

export function formatDateTime(value: string | Date | null | undefined, pattern = "dd MMM yyyy HH:mm"): string {
  const d = parseUtc(value);
  return d ? format(d, pattern) : DASH;
}

export function formatDate(value: string | Date | null | undefined): string {
  return formatDateTime(value, "dd MMM yyyy");
}

export function formatTime(value: string | Date | null | undefined): string {
  return formatDateTime(value, "HH:mm");
}

/** "in 18 hours" / "3 days ago" relative to `now` (defaults to the current time). */
export function formatRelative(value: string | Date | null | undefined, now: Date = new Date()): string {
  const d = parseUtc(value);
  if (!d) return DASH;
  return formatDistanceStrict(d, now, { addSuffix: true });
}

/** Signed hours until the given instant; negative when it is in the past. */
export function hoursUntil(value: string | Date | null | undefined, now: Date = new Date()): number | null {
  const d = parseUtc(value);
  if (!d) return null;
  return differenceInMinutes(d, now) / 60;
}

export function hoursBetween(a: Date, b: Date): number {
  return (b.getTime() - a.getTime()) / 3_600_000;
}

export function addHours(d: Date, hours: number): Date {
  return dfAddHours(d, hours);
}

export function startOfLocalDay(d: Date): Date {
  return startOfDay(d);
}

export function clampDate(d: Date, min: Date, max: Date): Date {
  if (d < min) return min;
  if (d > max) return max;
  return d;
}

/** Human "due" label combining absolute and relative form: "12 Sep 14:00 (in 18h)". */
export function formatDue(value: string | Date | null | undefined, now: Date = new Date()): string {
  const d = parseUtc(value);
  if (!d) return DASH;
  const hours = hoursBetween(now, d);
  const rel = Math.abs(hours) < 48 ? `${hours < 0 ? "-" : ""}${Math.abs(hours).toFixed(0)}h` : `${(hours / 24).toFixed(0)}d`;
  return `${format(d, "dd MMM HH:mm")} (${hours < 0 ? "overdue " : "in "}${rel.replace("-", "")})`;
}

/** yyyy-MM-dd for date-keyed endpoints such as GET /schedule/{date}. */
export function toDateKey(d: Date): string {
  return format(d, "yyyy-MM-dd");
}
