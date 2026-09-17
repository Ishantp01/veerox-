import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { OrgPhoneNumber } from "@/lib/types";

export interface AdminOrg {
  id: string;
  name: string;
  license_status: "active" | "suspended" | "expired";
  license_expires_at: string | null;
  license_issued_at: string | null;
  license_duration_days: number | null;
  license_notes: string | null;
  seat_count: number;
  admin_email: string | null;
  admin_name: string | null;
  admin_mobile: string | null;
  created_at: string;
  // Plivo/Twilio/WhatsApp entries alike — see apps/api/db/models/org_phone_number.py.
  phone_numbers: OrgPhoneNumber[];
}

// Input shape for one number in ProvisionOrgInput/UpdateOrgInput's
// phone_numbers array — no `id`/`created_at` since the server assigns those.
export interface OrgPhoneNumberInput {
  provider: "plivo" | "twilio" | "whatsapp";
  phone_number: string;
  is_default?: boolean;
}

/** GET /billing/orgs → AdminOrg[] (platform-admin only) */
export function useAdminOrgs() {
  return useQuery<AdminOrg[]>({
    queryKey: ["admin", "orgs"],
    queryFn: () => apiFetch<AdminOrg[]>("/billing/orgs"),
  });
}

export interface ProvisionOrgInput {
  org_name: string;
  email: string;
  full_name?: string;
  mobile: string;
  // Optional dedicated numbers for this org — any mix of Plivo/Twilio/
  // WhatsApp entries, several per provider allowed. Omit/empty to use the
  // platform default numbers (see apps/api/schemas/auth.py's ProvisionOrgIn)
  // — can be set later from the Edit dialog.
  phone_numbers?: OrgPhoneNumberInput[];
  // Optional — this org's own Plivo/Twilio/Meta WhatsApp credentials, set
  // now instead of (or in addition to, if changed later) the org's own
  // settings page. There's no platform-wide fallback — see
  // apps/api/core/org_credentials.py.
  plivo_auth_id?: string;
  plivo_auth_token?: string;
  twilio_account_sid?: string;
  twilio_auth_token?: string;
  meta_app_id?: string;
  meta_app_secret?: string;
  meta_access_token?: string;
  meta_whatsapp_business_account_id?: string;
  meta_verify_token?: string;
  // Optional — this org's own OpenAI key, set now instead of (or in
  // addition to, if changed later) PUT /admin/settings/openai-key. Left
  // unset, the org bills against the platform's shared key.
  openai_api_key?: string;
}

export interface ProvisionOrgResult {
  org_id: string;
  account_user_id: string;
  email: string;
  // Shown exactly once — hand both this and the email to the organization
  // so its admin can log in (see apps/api/routers/auth.py's provision_org).
  login_token: string;
  // True when the login token was also SMS'd to the mobile number.
  sms_sent: boolean;
}

/** POST /auth/provision-org → ProvisionOrgResult (platform-admin only) */
export function useProvisionOrg() {
  const queryClient = useQueryClient();
  return useMutation<ProvisionOrgResult, Error, ProvisionOrgInput>({
    mutationFn: (body) =>
      apiFetch<ProvisionOrgResult>("/auth/provision-org", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "orgs"] });
    },
  });
}

export interface RegenerateAdminTokenResult {
  account_user_id: string;
  email: string;
  // Shown exactly once — the previous token stops working immediately.
  login_token: string;
}

/** POST /billing/orgs/{orgId}/regenerate-admin-token → RegenerateAdminTokenResult
 * (platform-admin only). The original token can never be shown again since
 * only its hash is stored — this issues a brand new one instead. */
export function useRegenerateAdminToken() {
  return useMutation<RegenerateAdminTokenResult, Error, string>({
    mutationFn: (orgId) =>
      apiFetch<RegenerateAdminTokenResult>(`/billing/orgs/${orgId}/regenerate-admin-token`, {
        method: "POST",
      }),
  });
}

export interface UpdateOrgInput {
  orgId: string;
  name?: string;
  admin_email?: string;
  admin_name?: string;
  admin_mobile?: string;
  // Omitted = the org's numbers (Plivo, Twilio, and WhatsApp alike) are
  // left untouched; present (including []) = its full number set is
  // replaced with this one.
  phone_numbers?: OrgPhoneNumberInput[];
  // Optional — same "both halves of a pair or neither" rule as
  // ProvisionOrgInput. Omitting a pair leaves that provider's stored
  // credentials untouched; there's no way to read them back once
  // encrypted, so these always start blank in the edit form.
  plivo_auth_id?: string;
  plivo_auth_token?: string;
  twilio_account_sid?: string;
  twilio_auth_token?: string;
  meta_app_id?: string;
  meta_app_secret?: string;
  meta_access_token?: string;
  meta_whatsapp_business_account_id?: string;
  meta_verify_token?: string;
}

/**
 * PATCH /billing/orgs/{orgId} → AdminOrg (platform-admin only). Edits the
 * org's own profile fields only — license fields stay driven by the
 * dedicated POST /billing/orgs/{id}/license/* actions (see
 * apps/api/schemas/billing.py's OrgUpdateIn).
 */
export function useUpdateOrgAdmin() {
  const queryClient = useQueryClient();
  return useMutation<AdminOrg, Error, UpdateOrgInput>({
    mutationFn: ({ orgId, ...body }) =>
      apiFetch<AdminOrg>(`/billing/orgs/${orgId}`, { method: "PATCH", body: JSON.stringify(body) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "orgs"] });
    },
  });
}

/**
 * DELETE /billing/orgs/{orgId} → void (platform-admin only). Irreversible:
 * hard-deletes that org and everything under it (see
 * apps/api/routers/billing.py::delete_org). The platform's own operating
 * org is never a valid target — it's excluded from useAdminOrgs() already,
 * and the backend refuses it too.
 */
export function useDeleteOrgAdmin() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (orgId) => apiFetch<void>(`/billing/orgs/${orgId}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "orgs"] });
    },
  });
}

// Shared by all five license actions below — each hits its own POST
// /billing/orgs/{orgId}/license/* endpoint (see apps/api/routers/billing.py)
// and refreshes the org directory on success.
function useLicenseAction<TInput extends { orgId: string }>(action: string) {
  const queryClient = useQueryClient();
  return useMutation<AdminOrg, Error, TInput>({
    mutationFn: ({ orgId, ...body }) =>
      apiFetch<AdminOrg>(`/billing/orgs/${orgId}/license/${action}`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "orgs"] });
    },
  });
}

/** First-time (or from-scratch) license grant, active for `days` from now. */
export function useIssueLicense() {
  return useLicenseAction<{ orgId: string; days: number; notes?: string }>("issue");
}

/** Set a new expiry `days` from now and bring the license back to active.
 * `days` omitted = reuse the org's last issued/renewed duration (or a
 * 30-day default) — what a one-click renew (QuickRenewButton) sends. */
export function useRenewLicense() {
  return useLicenseAction<{ orgId: string; days?: number }>("renew");
}

/** Add `days` on top of the org's current expiry. */
export function useExtendLicense() {
  return useLicenseAction<{ orgId: string; days: number }>("extend");
}

/** Immediately lock the org out regardless of its expiry date. */
export function useSuspendLicense() {
  return useLicenseAction<{ orgId: string; notes?: string }>("suspend");
}

/** Lift a manual suspension. `days` omitted = reuse the org's last issued/
 * renewed duration (same fallback as renew) if a fresh expiry is needed. */
export function useReactivateLicense() {
  return useLicenseAction<{ orgId: string; days?: number }>("reactivate");
}
