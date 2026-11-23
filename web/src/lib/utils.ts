import { currentLocale } from "../i18n";

/** Fallback until the config loads; afterwards the owner's `proactive.timezone`. */
export const DEFAULT_TIME_ZONE = "Asia/Shanghai";
let ownerZone = DEFAULT_TIME_ZONE;

/** The owner's time zone, used for every date the console shows. */
export function ownerTimeZone(): string {
  return ownerZone;
}

export function setOwnerTimeZone(zone: string | null | undefined) {
  if (!zone) return;
  try {
    new Intl.DateTimeFormat("en", { timeZone: zone });
    ownerZone = zone;
  } catch {
    // An invalid IANA name keeps the previous zone rather than breaking every page.
  }
}

const formatters = new Map<string, Intl.DateTimeFormat>();

/**
 * A cached formatter in the owner time zone. `locale` defaults to the interface
 * language; pass a fixed one (such as "en-CA" for YYYY-MM-DD) for machine formats.
 */
export function dateFormat(
  options: Intl.DateTimeFormatOptions,
  fixedLocale?: string,
): Intl.DateTimeFormat {
  const locale = fixedLocale ?? currentLocale();
  const zone = options.timeZone ?? ownerZone;
  const key = `${locale}|${zone}|${JSON.stringify(options)}`;
  let format = formatters.get(key);
  if (!format) {
    format = new Intl.DateTimeFormat(locale, { ...options, timeZone: zone });
    formatters.set(key, format);
  }
  return format;
}

export function uniqueId(base: string, rows: { id: string }[]): string {
  const safe =
    base.replace(/[^a-zA-Z0-9_.-]/g, "-").slice(0, 110) || "instance";
  let id = safe;
  let suffix = 2;
  while (rows.some((row) => row.id === id)) id = `${safe}-${suffix++}`;
  return id;
}

export function splitLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function isSecretRef(value: string): boolean {
  return /^(secret:|env:)/.test(value);
}

export function formatDateTime(
  value: string | number | null | undefined,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const date =
    typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return dateFormat({
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return dateFormat({
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatBucketTime(value: string, daily: boolean): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : dateFormat(
        daily
          ? { month: "2-digit", day: "2-digit" }
          : {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              hour12: false,
            },
      ).format(date);
}

export function formatNumber(
  value: number,
  options?: Intl.NumberFormatOptions,
): string {
  return Number(value || 0).toLocaleString(currentLocale(), options);
}

/** Accepts the value from a datetime-local input and converts it to ISO. */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
