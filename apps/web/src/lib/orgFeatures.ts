// Mirrors apps/api/db/models/org.py's AVAILABLE_ORG_FEATURES — keep in sync.
// Each key gates one router (see apps/api/deps.py's require_feature).
export const ORG_FEATURES: { key: string; label: string }[] = [
  { key: "crm", label: "CRM (contacts)" },
  { key: "appointments", label: "Appointments" },
  { key: "follow_ups", label: "Follow-ups" },
  { key: "sales", label: "Sales pipeline & revenue" },
  { key: "helpdesk", label: "Helpdesk widget" },
  { key: "tickets", label: "Support tickets" },
  { key: "templates", label: "WhatsApp templates" },
  { key: "conversations", label: "Conversations inbox" },
];

/** True once `enabledFeatures` (from MeInfo.enabled_features) explicitly
 * excludes `feature` — null means unrestricted, every feature allowed. */
export function isFeatureDisabled(enabledFeatures: string[] | null, feature: string): boolean {
  return enabledFeatures !== null && !enabledFeatures.includes(feature);
}

// Dashboard route prefixes gated behind a feature — mirrors the routers
// wired to apps/api/deps.py's require_feature. Used to hide the
// corresponding sidebar item (components/nav.tsx) and to bounce a direct
// URL visit back to "/" (app/(dashboard)/layout.tsx), same defense-in-depth
// pattern as MEMBER_RESTRICTED_HREFS/MEMBER_RESTRICTED_PREFIXES. Routes with
// no feature dependency (leads, calling, campaigns, team, settings, ...)
// are intentionally left out.
export const FEATURE_GATED_ROUTES: { prefix: string; feature: string }[] = [
  { prefix: "/crm/contacts", feature: "crm" },
  { prefix: "/crm/appointments", feature: "appointments" },
  { prefix: "/conversations", feature: "conversations" },
  { prefix: "/automation/follow-ups", feature: "follow_ups" },
  { prefix: "/analytics/sales", feature: "sales" },
  { prefix: "/whatsapp/templates", feature: "templates" },
  { prefix: "/support", feature: "tickets" },
];

/** The feature key gating this pathname, if any — first matching prefix
 * wins (exact match or "/prefix/..."). */
export function featureForRoute(pathname: string): string | null {
  const match = FEATURE_GATED_ROUTES.find(
    ({ prefix }) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
  return match?.feature ?? null;
}
