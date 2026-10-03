const HAS_TIMEZONE = /(Z|[+-]\d{2}:?\d{2})$/i;

/**
 * The backend stores UTC. PostgreSQL returns timestamps with an offset but
 * SQLite returns them without one, which `new Date` would read as local
 * time, so a missing offset is taken as UTC.
 */
export function parseTimestamp(value: string): Date {
  return new Date(HAS_TIMEZONE.test(value) ? value : `${value}Z`);
}

export function formatDateTime(
  value: string | null | undefined,
  timeZone?: string,
): string {
  if (!value) return "-";

  const date = parseTimestamp(value);

  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone,
  }).format(date);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "-";

  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;

  if (seconds < 60) return `${seconds.toFixed(1)} s`;

  const minutes = Math.floor(seconds / 60);

  if (seconds < 3600) return `${minutes}m ${Math.round(seconds % 60)}s`;

  return `${Math.floor(seconds / 3600)}h ${minutes % 60}m`;
}

export function formatCurrency(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "-";

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
  }).format(amount);
}

/** 0.4286 -> "43%". */
export function formatPercent(share: number | null | undefined, digits = 0): string {
  if (share === null || share === undefined) return "-";

  return `${(share * 100).toFixed(digits)}%`;
}

/** "awaiting_approval" -> "Awaiting approval". */
export function humanize(value: string): string {
  const text = value.replace(/[_-]+/g, " ").trim();

  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Value of an <input type="datetime-local"> to an ISO string for the API. */
export function localInputToIso(value: string): string | undefined {
  if (!value) return undefined;

  const date = new Date(value);

  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function shortId(threadId: string): string {
  return threadId.slice(0, 8);
}
