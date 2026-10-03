import { describe, expect, test } from "vitest";

import {
  formatCurrency,
  formatDateTime,
  formatDuration,
  formatPercent,
  humanize,
  localInputToIso,
  parseTimestamp,
  shortId,
} from "./format";

describe("parseTimestamp", () => {
  test("keeps an explicit offset", () => {
    expect(parseTimestamp("2026-10-01T09:00:00+00:00").toISOString()).toBe(
      "2026-10-01T09:00:00.000Z",
    );
    expect(parseTimestamp("2026-10-01T09:00:00+02:00").toISOString()).toBe(
      "2026-10-01T07:00:00.000Z",
    );
    expect(parseTimestamp("2026-10-01T09:00:00Z").toISOString()).toBe("2026-10-01T09:00:00.000Z");
  });

  test("reads a timestamp without an offset (SQLite) as UTC", () => {
    expect(parseTimestamp("2026-10-01T09:00:00").toISOString()).toBe("2026-10-01T09:00:00.000Z");
    expect(parseTimestamp("2026-10-01T09:00:00.123456").toISOString()).toBe(
      "2026-10-01T09:00:00.123Z",
    );
  });
});

describe("formatDateTime", () => {
  test("formats in the requested time zone", () => {
    expect(formatDateTime("2026-10-01T09:05:07+00:00", "UTC")).toBe("01 Oct 2026, 09:05:07");
  });

  test("shows a dash for missing values and echoes unparseable ones", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime(undefined)).toBe("-");
    expect(formatDateTime("")).toBe("-");
    expect(formatDateTime("not a date")).toBe("not a date");
  });
});

describe("formatDuration", () => {
  test.each([
    [0.35, "350 ms"],
    [0, "0 ms"],
    [1, "1.0 s"],
    [59.94, "59.9 s"],
    [60, "1m 0s"],
    [200, "3m 20s"],
    [3600, "1h 0m"],
    [7500, "2h 5m"],
  ])("%s seconds -> %s", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });

  test("handles missing values", () => {
    expect(formatDuration(null)).toBe("-");
    expect(formatDuration(undefined)).toBe("-");
    expect(formatDuration(Number.NaN)).toBe("-");
  });
});

describe("formatCurrency", () => {
  test("formats whole and fractional amounts", () => {
    expect(formatCurrency(750)).toBe("$750");
    expect(formatCurrency(6000)).toBe("$6,000");
    expect(formatCurrency(1234.5)).toBe("$1,234.50");
    expect(formatCurrency(0)).toBe("$0");
  });

  test("shows a dash when there is no amount", () => {
    expect(formatCurrency(null)).toBe("-");
    expect(formatCurrency(undefined)).toBe("-");
  });
});

describe("other formatters", () => {
  test("formatPercent", () => {
    expect(formatPercent(0.4286)).toBe("43%");
    expect(formatPercent(0.4286, 1)).toBe("42.9%");
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(null)).toBe("-");
  });

  test("humanize", () => {
    expect(humanize("awaiting_approval")).toBe("Awaiting approval");
    expect(humanize("node-failed")).toBe("Node failed");
    expect(humanize("high")).toBe("High");
    expect(humanize("")).toBe("");
  });

  test("localInputToIso", () => {
    expect(localInputToIso("")).toBeUndefined();
    expect(localInputToIso("garbage")).toBeUndefined();
    expect(localInputToIso("2026-10-01T09:00")).toMatch(/^2026-10-01T\d{2}:\d{2}:00\.000Z$/);
  });

  test("shortId", () => {
    expect(shortId("11111111-2222-3333-4444-555555555555")).toBe("11111111");
  });
});
