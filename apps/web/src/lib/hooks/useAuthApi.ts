import { apiFetch } from "@/lib/api";

export interface SessionInfo {
  token: string;
  org_id: string;
  org_name: string;
  role: "admin" | "member";
  account_user_id: string;
  email: string;
  full_name: string | null;
  is_superuser: boolean;
  // True when org_id is the platform operator's own seeded org — every
  // Veerox staff account (not just the superuser) gets this. Gates
  // platform-team-only pages like the cross-org support ticket queue.
  is_platform_org: boolean;
  // Display-only — deps.py's enforce_org_license is the real enforcement.
  // Always "active"/null for is_platform_org.
  license_status: "active" | "suspended" | "expired";
  license_expires_at: string | null;
  // Display-only, same role as license_status above — deps.py's
  // require_feature is the real enforcement. null = unrestricted (every
  // feature allowed). Always null for is_platform_org. See
  // apps/web/src/lib/orgFeatures.ts for the known keys.
  enabled_features: string[] | null;
}

export interface MeInfo {
  org_id: string;
  org_name: string;
  role: "admin" | "member";
  account_user_id: string;
  email: string;
  full_name: string | null;
  is_superuser: boolean;
  is_platform_org: boolean;
  license_status: "active" | "suspended" | "expired";
  license_expires_at: string | null;
  enabled_features: string[] | null;
}

/** POST /auth/login → SessionInfo. Login token is the sole credential — no
 * email/password; accounts are only ever created by an admin. */
export function login(loginToken: string): Promise<SessionInfo> {
  return apiFetch<SessionInfo>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ token: loginToken }),
  });
}

/** POST /auth/logout — best-effort, caller clears local state regardless. */
export function logoutRequest(): Promise<void> {
  return apiFetch<void>("/auth/logout", { method: "POST" });
}

/** GET /auth/me → MeInfo — used to hydrate/validate a stored session token. */
export function fetchMe(): Promise<MeInfo> {
  return apiFetch<MeInfo>("/auth/me");
}

/** POST /auth/forgot-token — always resolves with a generic message; never
 * reveals whether the identifier matched an account. */
export function forgotToken(identifier: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>("/auth/forgot-token", {
    method: "POST",
    body: JSON.stringify({ identifier }),
  });
}
