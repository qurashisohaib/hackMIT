import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind-aware class merger (shadcn convention). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

// ---------------------------------------------------------------- numbers

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const usdWhole = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

const usdCompact = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

const intFmt = new Intl.NumberFormat("en-US");
const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export interface MoneyOptions {
  /** Drop cents. Default false. */
  whole?: boolean;
  /** Compact notation ($4.2M). Default false. */
  compact?: boolean;
  /** Prefix "+" for positive values. Default false. */
  signed?: boolean;
}

/** Format a USD amount. Null/undefined/NaN → "—". */
export function formatMoney(value: number | null | undefined, opts: MoneyOptions = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const fmt = opts.compact ? usdCompact : opts.whole ? usdWhole : usd;
  const abs = fmt.format(Math.abs(value));
  if (value < 0) return `−${abs}`;
  return opts.signed && value > 0 ? `+${abs}` : abs;
}

/** Format a 0..1 ratio as a percentage. `digits` defaults to 0 ("94%"). */
export function formatPercent(
  ratio: number | null | undefined,
  digits = 0,
  opts: { signed?: boolean } = {},
): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "—";
  const pct = ratio * 100;
  const text = `${Math.abs(pct).toFixed(digits)}%`;
  if (pct < 0) return `−${text}`;
  return opts.signed && pct > 0 ? `+${text}` : text;
}

/** Format a percentage-point delta (already in points, e.g. 3.2 → "+3.2 pp"). */
export function formatPoints(points: number | null | undefined, digits = 1): string {
  if (points === null || points === undefined || Number.isNaN(points)) return "—";
  const text = `${Math.abs(points).toFixed(digits)} pp`;
  if (points < 0) return `−${text}`;
  if (points > 0) return `+${text}`;
  return `0 pp`;
}

/** Thousands-separated integer. */
export function formatNumber(value: number | null | undefined, opts: { signed?: boolean } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const text = intFmt.format(Math.abs(value));
  if (value < 0) return `−${text}`;
  return opts.signed && value > 0 ? `+${text}` : text;
}

/** 12.9K / 4.2M style. */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return compactFmt.format(value);
}

/** Fixed decimals, "—" when missing. */
export function formatDecimal(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

/** Milliseconds → "842 ms" / "1.3 s" / "2m 05s". */
export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// ---------------------------------------------------------------- dates

const dateFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
const dateShortFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const timeFmt = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Jan 31, 2026" */
export function formatDate(value: string | number | Date | null | undefined): string {
  const d = toDate(value);
  return d ? dateFmt.format(d) : "—";
}

/** "Jan 31" */
export function formatDateShort(value: string | number | Date | null | undefined): string {
  const d = toDate(value);
  return d ? dateShortFmt.format(d) : "—";
}

/** "Jan 31, 4:12 PM" */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  const d = toDate(value);
  return d ? dateTimeFmt.format(d) : "—";
}

/** "16:12:08" — for event tickers. */
export function formatTime(value: string | number | Date | null | undefined): string {
  const d = toDate(value);
  return d ? timeFmt.format(d) : "—";
}

/** "3s ago", "2m ago", "4h ago", else a short date. */
export function formatRelative(value: string | number | Date | null | undefined, now = Date.now()): string {
  const d = toDate(value);
  if (!d) return "—";
  const diff = Math.max(0, now - d.getTime());
  const s = Math.floor(diff / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return formatDateShort(d);
}

// ---------------------------------------------------------------- strings

/** "processor_fee" → "Processor fee" */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const s = value.replace(/[_.-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "Processor Fee" style. */
export function titleCase(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .replace(/[_.-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Shorten long ids for display: "EXC-3f9a1b2c4d" → "EXC-3f9a…". */
export function shortId(id: string | null | undefined, keep = 8): string {
  if (!id) return "—";
  return id.length <= keep + 1 ? id : `${id.slice(0, keep)}…`;
}

export function truncate(text: string | null | undefined, max = 80): string {
  if (!text) return "";
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** Period id → month label ("2026-01" → "January"); falls back to the id. */
export function periodMonth(period: { id?: string; name?: string; start_date?: string } | string | null | undefined): string {
  if (!period) return "";
  const src = typeof period === "string" ? period : period.start_date ?? period.id ?? period.name ?? "";
  const m = /(\d{4})-(\d{2})/.exec(src);
  if (m) {
    const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 1));
    return d.toLocaleString("en-US", { month: "long", timeZone: "UTC" });
  }
  return typeof period === "string" ? period : period.name ?? period.id ?? "";
}

/** Short month: "Jan". */
export function periodMonthShort(period: { id?: string; name?: string; start_date?: string } | string | null | undefined): string {
  return periodMonth(period).slice(0, 3);
}

// ---------------------------------------------------------------- charts

/**
 * Validated categorical chart palette for the dark surface (dataviz skill, all-pairs
 * check passes for 4 slots against #101012). Assign in fixed order; never cycle.
 */
export const CHART_SERIES = ["#0ea371", "#8b5cf6", "#0284c7", "#d97706"] as const;

/** Chart chrome tokens matching globals.css. */
export const CHART_CHROME = {
  grid: "#232329",
  axis: "#2e2e36",
  muted: "#8a8a96",
  ink: "#ededf0",
  surface: "#101012",
} as const;
