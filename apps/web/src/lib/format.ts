/**
 * Presentation formatters. Pure, dependency-free, unit-testable in isolation
 * (covered by Vitest in Wave 3). The UI never formats inline — it calls these
 * so date/phone/currency rendering stays consistent across every surface.
 */

/** Locale date-time, e.g. "29 May 2026, 3:52 pm". Returns "—" for null/invalid. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Relative time, e.g. "just now", "3m ago", "2h ago", "5d ago". */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 45) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

/** Group an E.164-ish phone for readability; falls back to the raw string. */
export function formatPhone(phone: string | null | undefined): string {
  if (!phone) return "—";
  const digits = phone.replace(/[^\d+]/g, "");
  // +91 98765 43210 style grouping for Indian numbers; otherwise return cleaned.
  const m = digits.match(/^(\+\d{1,3})(\d{5})(\d{5})$/);
  return m ? `${m[1]} ${m[2]} ${m[3]}` : digits;
}

/** USD with 2 decimals, e.g. "$1.53". Returns "$0.00" for null. */
export function formatUsd(amount: number | null | undefined): string {
  const n = typeof amount === "number" && !Number.isNaN(amount) ? amount : 0;
  return `$${n.toFixed(2)}`;
}

/** Short date for chart axis ticks / table rows, e.g. "15 Jul". Accepts a
 * plain "YYYY-MM-DD" bucket key (as returned by GET /admin/reports/timeseries)
 * or a full ISO timestamp. Returns "—" for null/invalid. */
export function formatShortDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "—";
  const d = new Date(dateStr.length <= 10 ? `${dateStr}T00:00:00` : dateStr);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

/** Percentage with 0 decimals, e.g. "42%". Returns "—" for null. */
export function formatPercent(ratio: number | null | undefined): string {
  if (ratio == null || Number.isNaN(ratio)) return "—";
  return `${Math.round(ratio * 100)}%`;
}

/** Seconds → "Xm Ys" / "Ys". Returns "—" for null. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}
