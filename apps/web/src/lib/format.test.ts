import { describe, expect, it } from "vitest";
import {
  formatDateTime,
  formatDuration,
  formatPhone,
  formatRelative,
  formatUsd,
} from "./format";

describe("formatDateTime", () => {
  it("returns em dash for null/undefined/invalid", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime(undefined)).toBe("—");
    expect(formatDateTime("not-a-date")).toBe("—");
  });

  it("renders a valid ISO string (contains year + month)", () => {
    const out = formatDateTime("2026-05-29T10:22:00Z");
    expect(out).toContain("2026");
    expect(out).toMatch(/May/);
  });
});

describe("formatRelative", () => {
  it("returns em dash for null", () => {
    expect(formatRelative(null)).toBe("—");
  });

  it("says 'just now' for the current moment", () => {
    expect(formatRelative(new Date().toISOString())).toBe("just now");
  });

  it("renders minutes for a few minutes ago", () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    expect(formatRelative(fiveMinAgo)).toBe("5m ago");
  });

  it("renders hours for a few hours ago", () => {
    const threeHrsAgo = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
    expect(formatRelative(threeHrsAgo)).toBe("3h ago");
  });
});

describe("formatPhone", () => {
  it("groups a +91 number", () => {
    expect(formatPhone("+919876543210")).toBe("+91 98765 43210");
  });

  it("treats a 91-prefixed number stored without + the same as +91", () => {
    expect(formatPhone("919179609988")).toBe("+91 91796 09988");
    expect(formatPhone("+919179609988")).toBe("+91 91796 09988");
  });

  it("strips junk but returns cleaned digits when no grouping match", () => {
    expect(formatPhone("98765-43210")).toBe("9876543210");
  });

  it("returns em dash for null", () => {
    expect(formatPhone(null)).toBe("—");
  });
});

describe("formatUsd", () => {
  it("formats with two decimals", () => {
    expect(formatUsd(1.5)).toBe("$1.50");
    expect(formatUsd(0)).toBe("$0.00");
  });

  it("treats null/NaN as zero", () => {
    expect(formatUsd(null)).toBe("$0.00");
    expect(formatUsd(Number.NaN)).toBe("$0.00");
  });
});

describe("formatDuration", () => {
  it("returns em dash for null", () => {
    expect(formatDuration(null)).toBe("—");
  });

  it("renders seconds under a minute", () => {
    expect(formatDuration(45)).toBe("45s");
  });

  it("renders minutes + seconds over a minute", () => {
    expect(formatDuration(125)).toBe("2m 5s");
  });
});
