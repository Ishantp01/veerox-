export const SESSION_TOKEN_KEY = "veerox_session_token";
export const AUTH_MODE_KEY = "veerox_auth_mode";
export type AuthMode = "session" | "admin";

/**
 * Fired on `window` whenever the API answers 402 — the org has run out of
 * plan credit (or its paid period lapsed) and the request was refused. The
 * credit modal (components/billing/credit-expired-modal.tsx) listens for
 * this so the block surfaces the instant it happens, instead of waiting up
 * to POLL.billing ms for /billing/status to catch up.
 */
export const CREDIT_LIMIT_EVENT = "veerox:credit-limit";

/**
 * Read the dashboard session token from localStorage.
 * Returns an empty string when called during SSR (localStorage is unavailable).
 */
function getToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(SESSION_TOKEN_KEY) ?? "";
}

function getAuthMode(): AuthMode {
  if (typeof window === "undefined") return "session";
  const mode = localStorage.getItem(AUTH_MODE_KEY);
  return mode === "admin" ? "admin" : "session";
}

/**
 * Plain-language fallback per HTTP status, used whenever the backend's
 * error text isn't safe to show a non-technical client as-is.
 */
const STATUS_FALLBACK: Record<number, string> = {
  400: "That didn't go through — please check the details and try again.",
  401: "Your session has expired. Please log in again.",
  403: "You don't have permission to do that.",
  404: "We couldn't find that — it may have been deleted or moved.",
  409: "That already exists or conflicts with something else.",
  422: "That didn't go through — please check the details and try again.",
  429: "Too many requests — please wait a moment and try again.",
  500: "Something went wrong on our end. Please try again in a moment.",
  502: "Something went wrong on our end. Please try again in a moment.",
  503: "The service is temporarily unavailable. Please try again shortly.",
};
const DEFAULT_FALLBACK = "Something went wrong. Please try again.";

/** Backend error text that reads like it was written for a developer, not a client. */
const TECHNICAL_MESSAGE_RE =
  /traceback|exception|stack trace|nonetype|keyerror|valueerror|typeerror|sqlalchemy|psycopg|integrityerror|constraint|null value|undefined|internal server error|^api error \d+:|\{["'][a-z_]+["']:/i;

/**
 * Every toast in the app shows `err.message` from a failed `apiFetch` call
 * verbatim (23+ call sites) — this is the one place all of them pass
 * through, so it's the one place to catch a raw exception/DB error before
 * it reaches a non-technical client. Curated backend messages (short,
 * plain-English HTTPException details) pass through unchanged; anything
 * that looks like developer-facing text gets swapped for a status-based
 * fallback instead.
 */
function humanizeApiError(status: number, raw: string): string {
  const trimmed = raw.trim();
  const fallback = STATUS_FALLBACK[status] ?? DEFAULT_FALLBACK;
  if (!trimmed) return fallback;
  if (trimmed.length > 180 || TECHNICAL_MESSAGE_RE.test(trimmed)) return fallback;
  return trimmed;
}

/**
 * Typed fetch wrapper for the Veerox API.
 *
 * - Prepends NEXT_PUBLIC_API_URL to `path`.
 * - Injects `X-Session-Token` for normal sessions or `X-Admin-Token` for the
 *   owner-org admin token.
 * - Throws a descriptive Error on any non-2xx response.
 * - Returns the parsed JSON body as T.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";
  const url = `${base}${path}`;

  const token = getToken();
  const authMode = getAuthMode();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) {
    headers[authMode === "admin" ? "X-Admin-Token" : "X-Session-Token"] = token;
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
    message = humanizeApiError(response.status, message);
    // Announce a plan-credit refusal to whoever is listening (the credit
    // modal), regardless of which page made the call — every feature route
    // goes through this wrapper, so the block can't be missed just because
    // the calling page only rendered a toast.
    if (response.status === 402 && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(CREDIT_LIMIT_EVENT, { detail: { message } }));
    }
    // Status is attached (not just embedded in the message) so callers —
    // notably the query client's retry policy — can tell a permanent auth
    // failure (401/403) apart from a transient one without string-matching.
    throw Object.assign(new Error(message), { status: response.status });
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
