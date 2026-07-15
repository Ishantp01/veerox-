const TOKEN_KEY = "veerox_admin_token";

/**
 * Trigger a browser download of a CSV endpoint that requires the
 * X-Admin-Token header (so a plain `<a href>` won't work) — fetch with the
 * header, read the body as a Blob, and download it via an object URL. Mirrors
 * lib/api.ts's token + base-URL logic rather than importing apiFetch, which
 * always sets a JSON Content-Type/Accept unsuited to a file download.
 */
export async function downloadCsv(path: string, filename: string): Promise<void> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
  const token = typeof window === "undefined" ? "" : localStorage.getItem(TOKEN_KEY) ?? "";

  const headers: Record<string, string> = {};
  if (token) headers["X-Admin-Token"] = token;

  const res = await fetch(`${base}${path}`, { headers });
  if (!res.ok) {
    throw new Error(`Export failed (${res.status} ${res.statusText})`);
  }

  const blob = await res.blob();
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
}
