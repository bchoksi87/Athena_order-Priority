/**
 * Number, currency and duration formatters. All functions are null-tolerant
 * and return an em dash for missing values so tables stay aligned.
 */

export const DASH = "—";

export type IndianScale = "none" | "lakh" | "crore" | "auto";

export interface CurrencyOptions {
  /** Compact using Indian numbering (lakh/crore). "auto" picks by magnitude. */
  scale?: IndianScale;
  /** Number of fraction digits when compacted. */
  digits?: number;
  /** ISO currency code; INR renders the rupee sign. */
  currency?: string;
}

const LAKH = 100_000;
const CRORE = 10_000_000;

function currencySymbol(code: string): string {
  switch (code.toUpperCase()) {
    case "INR":
      return "₹";
    case "USD":
      return "$";
    case "EUR":
      return "€";
    case "GBP":
      return "£";
    default:
      return `${code} `;
  }
}

const inrGrouping = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** Formats INR (or another currency) with optional lakh/crore compaction: ₹42.6 L, ₹1.2 Cr. */
export function formatCurrency(
  value: number | null | undefined,
  { scale = "auto", digits = 1, currency = "INR" }: CurrencyOptions = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const sym = currencySymbol(currency);
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  const useIndian = currency.toUpperCase() === "INR";
  let effective = scale;
  if (effective === "auto") {
    if (!useIndian) effective = "none";
    else if (abs >= CRORE) effective = "crore";
    else if (abs >= LAKH) effective = "lakh";
    else effective = "none";
  }
  if (effective === "crore") return `${sign}${sym}${(abs / CRORE).toFixed(digits)} Cr`;
  if (effective === "lakh") return `${sign}${sym}${(abs / LAKH).toFixed(digits)} L`;
  const grouped = useIndian
    ? inrGrouping.format(Math.round(abs))
    : new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Math.round(abs));
  return `${sign}${sym}${grouped}`;
}

/** Plain number with thousands separators and fixed fraction digits. */
export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

/** Percentage from a 0..100 value: 87.4 -> "87.4%". */
export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return `${value.toFixed(digits)}%`;
}

/** Percentage from a 0..1 ratio: 0.874 -> "87.4%". */
export function formatRatio(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return formatPct(value * 100, digits);
}

/** Minutes as a compact duration: 135 -> "2h 15m", 2880 -> "2d 0h". */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return DASH;
  const sign = minutes < 0 ? "-" : "";
  const total = Math.round(Math.abs(minutes));
  if (total < 60) return `${sign}${total}m`;
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  if (hours < 48) return `${sign}${hours}h ${mins.toString().padStart(2, "0")}m`;
  const days = Math.floor(hours / 24);
  return `${sign}${days}d ${hours % 24}h`;
}

/** Hours as a duration: 2.4 -> "2.4h"; larger values switch to days. */
export function formatHours(hours: number | null | undefined, digits = 1): string {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return DASH;
  const abs = Math.abs(hours);
  const sign = hours < 0 ? "-" : "";
  if (abs >= 72) return `${sign}${(abs / 24).toFixed(1)}d`;
  return `${sign}${abs.toFixed(digits)}h`;
}

/** Signed number with explicit plus sign: 12.3 -> "+12.3", -3 -> "-3.0". */
export function formatSigned(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const fixed = Math.abs(value).toFixed(digits);
  if (value > 0) return `+${fixed}`;
  if (value < 0) return `-${fixed}`;
  return digits > 0 ? `0.${"0".repeat(digits)}` : "0";
}

/** Score in [0, 100] rendered as an integer. */
export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return Math.round(value).toString();
}

/** Delta between two values expressed as "87% → 94%". */
export function formatTransition(before: string, after: string): string {
  return `${before} → ${after}`;
}

/** Truncates an identifier for dense tables while keeping it recognisable. */
export function shortId(id: string | null | undefined, max = 14): string {
  if (!id) return DASH;
  return id.length <= max ? id : `${id.slice(0, max - 1)}…`;
}
