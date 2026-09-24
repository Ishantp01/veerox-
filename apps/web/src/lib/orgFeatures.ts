// Subset of apps/api/db/models/org.py's AVAILABLE_ORG_FEATURES exposed as
// toggles here. Deliberately excludes "crm" and "conversations" (no
// dedicated sidebar item of their own), Dashboard/Settings (Dashboard is
// "/", the same fallback every other disabled route redirects TO, so
// gating it has nowhere left to send a blocked visitor; Settings isn't one
// router but a bundle of endpoints across admin.py/billing.py with no
// single place to gate yet), and "tickets"/Support — kept enforced
// server-side (see FEATURE_GATED_ROUTES below) but not exposed as an
// admin-facing toggle. Each key gates one router (see apps/api/deps.py's
// require_feature).
export const ORG_FEATURES: { key: string; label: string }[] = [
  { key: "calling", label: "AI Calling" },
  { key: "whatsapp", label: "AI WhatsApp" },
  { key: "whatsapp_media", label: "WhatsApp Media" },
  { key: "leads", label: "Leads" },
  { key: "appointments", label: "Appointments" },
  { key: "campaigns_voice", label: "Voice Campaigns" },
  { key: "campaigns_whatsapp", label: "WhatsApp Campaigns" },
  { key: "follow_ups_voice", label: "Voice Follow-ups" },
  { key: "follow_ups_whatsapp", label: "WhatsApp Follow-ups" },
  { key: "sales", label: "Sales pipeline & revenue" },
  { key: "templates", label: "WhatsApp templates" },
  { key: "human_support", label: "Human Support (hand a lead to a human)" },
  { key: "follow_up_tasks", label: "Follow-up reminders & tasks" },
  { key: "reports", label: "Reports" },
  { key: "team", label: "Team" },
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
// no feature dependency (team, settings, dashboard, ...) are intentionally
// left out. Order matters: featureForRoute() below returns the FIRST
// matching prefix, so "/calling/..." and "/whatsapp/..." sub-paths with
// their own, more specific feature must be listed before the generic
// "/calling" and "/whatsapp" entries that would otherwise swallow them.
export const FEATURE_GATED_ROUTES: { prefix: string; feature: string }[] = [
  { prefix: "/crm/contacts", feature: "crm" },
  { prefix: "/crm/appointments", feature: "appointments" },
  { prefix: "/crm/leads", feature: "leads" },
  { prefix: "/conversations", feature: "conversations" },
  { prefix: "/human-support", feature: "human_support" },
  { prefix: "/calling/human-support", feature: "human_support" },
  { prefix: "/whatsapp/human-support", feature: "human_support" },
  { prefix: "/follow-up-tasks", feature: "follow_up_tasks" },
  { prefix: "/automation/follow-ups/voice", feature: "follow_ups_voice" },
  { prefix: "/automation/follow-ups/whatsapp", feature: "follow_ups_whatsapp" },
  { prefix: "/automation/campaigns/voice", feature: "campaigns_voice" },
  { prefix: "/automation/campaigns/whatsapp", feature: "campaigns_whatsapp" },
  { prefix: "/analytics/sales", feature: "sales" },
  { prefix: "/whatsapp/templates", feature: "templates" },
  { prefix: "/whatsapp/media", feature: "whatsapp_media" },
  { prefix: "/support", feature: "tickets" },
  { prefix: "/reports", feature: "reports" },
  { prefix: "/calling", feature: "calling" },
  { prefix: "/whatsapp", feature: "whatsapp" },
  { prefix: "/team", feature: "team" },
];

/** The feature key gating this pathname, if any — first matching prefix
 * wins (exact match or "/prefix/..."). */
export function featureForRoute(pathname: string): string | null {
  const match = FEATURE_GATED_ROUTES.find(
    ({ prefix }) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
  return match?.feature ?? null;
}
