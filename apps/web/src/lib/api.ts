const TOKEN_KEY = "veerox_admin_token";

/**
 * Read the admin token from localStorage.
 * Returns an empty string when called during SSR (localStorage is unavailable).
 */
function getToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

/**
 * Typed fetch wrapper for the Veerox admin API.
 *
 * - Prepends NEXT_PUBLIC_API_URL to `path`.
 * - Injects the X-Admin-Token header from localStorage.
 * - Throws a descriptive Error on any non-2xx response.
 * - Returns the parsed JSON body as T.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
  const url = `${base}${path}`;

  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) {
    headers["X-Admin-Token"] = token;
  }

  const response = await fetch(url, { ...init, headers });

  if (!response.ok) {
    let message = `API error ${response.status}: ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") {
        message = body.detail;
      } else if (typeof body?.message === "string") {
        message = body.message;
      }
    } catch {
      // ignore JSON parse failure — use the status message
    }
    // Status is attached (not just embedded in the message) so callers —
    // notably the query client's retry policy — can tell a permanent auth
    // failure (401/403) apart from a transient one without string-matching.
    throw Object.assign(new Error(message), { status: response.status });
  }

  return response.json() as Promise<T>;
}
