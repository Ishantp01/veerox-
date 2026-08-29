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
