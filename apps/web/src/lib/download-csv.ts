import { SESSION_TOKEN_KEY } from "./api";

/**
 * Trigger a browser download of a file endpoint that requires the
 * X-Session-Token header (so a plain `<a href>` won't work) — fetch with the
 * header, read the body as a Blob, and download it via an object URL. Mirrors
 * lib/api.ts's token + base-URL logic rather than importing apiFetch, which
 * always sets a JSON Content-Type/Accept unsuited to a file download.
 */
/**
 * Count data rows in a CSV payload (everything after the header line).
 * Deliberately naive about quoted embedded newlines — it only needs to
 * distinguish "header only" from "has data", not parse the file.
 */
function countDataRows(csv: string): number {
  return csv.split("\n").filter((line) => line.trim() !== "").length - 1;
}

export async function downloadCsv(
  path: string,
  filename: string
): Promise<{ rows: number | null }> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem(SESSION_TOKEN_KEY) ?? "";

  const headers: Record<string, string> = {};
  if (token) headers["X-Session-Token"] = token;

  const res = await fetch(`${base}${path}`, { headers });
  if (!res.ok) {
    // Surface the API's own `detail` when there is one — a bare "403 Forbidden"
    // hides whether the session is missing, expired, or under-privileged.
    let message = `Export failed (${res.status} ${res.statusText})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") {
        message = `${body.detail} (${res.status})`;
      }
    } catch {
      // non-JSON error body — keep the status message
    }
    throw Object.assign(new Error(message), { status: res.status });
  }

  const blob = await res.blob();

  // For CSV, report the data-row count back so the caller can tell the user
  // "nothing matched" instead of silently saving a header-only file. Binary
  // payloads (the .xlsx sample downloads) report null and always save.
  let rows: number | null = null;
  if (blob.type.includes("csv") || filename.endsWith(".csv")) {
    rows = countDataRows(await blob.text());
    if (rows <= 0) return { rows: 0 };
  }

  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
  return { rows };
}
