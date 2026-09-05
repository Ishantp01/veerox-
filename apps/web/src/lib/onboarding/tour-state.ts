/**
 * Tracks whether an account has already seen the guided product tour.
 *
 * There's no server-side "onboarding complete" flag, so this is a per-browser
 * marker keyed by the account user id (from GET /auth/me). Worst case a user
 * who clears storage or switches browsers sees the tour again — harmless, and
 * they can close it in one click. The key is bumped (`_v1`) so a future
 * revamped tour can re-run for everyone.
 */
const KEY_PREFIX = "veerox_tour_v1_";

function key(userId: string): string {
  return `${KEY_PREFIX}${userId}`;
}

/** True if the tour should NOT auto-start for this account. Fails closed (no
 *  nag) when localStorage is unavailable. */
export function hasSeenTour(userId: string): boolean {
  try {
    return localStorage.getItem(key(userId)) === "done";
  } catch {
    return true;
  }
}

/** Record that the tour has run (finished or dismissed) for this account. */
export function markTourSeen(userId: string): void {
  try {
    localStorage.setItem(key(userId), "done");
  } catch {
    /* private mode / storage disabled — nothing we can do, ignore */
  }
}
