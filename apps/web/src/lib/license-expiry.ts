import type { AdminOrg } from "@/lib/hooks/useAdminOrgs";

/** Default lookahead window for the "expiring soon" list/badge — an active
 * license whose expiry falls within this many days from now. */
export const EXPIRING_SOON_DAYS = 14;

/**
 * True if `org` has an active license expiring within `withinDays` — used
 * by both the Organizations page's badge/count and the dedicated
 * /organizations/expiring page. Already-expired or suspended orgs are
 * excluded; they belong to a different, more urgent bucket (the license
 * badge already flags those directly).
 */
export function isExpiringSoon(org: AdminOrg, withinDays: number): boolean {
  if (org.license_status !== "active" || !org.license_expires_at) return false;
  const msUntilExpiry = new Date(org.license_expires_at).getTime() - Date.now();
  return msUntilExpiry >= 0 && msUntilExpiry <= withinDays * 24 * 60 * 60 * 1000;
}
