import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface AdminOrg {
  id: string;
  name: string;
  plan_code: string | null;
  billing_status: string;
  seat_count: number;
  admin_email: string | null;
  created_at: string;
  plivo_phone_number: string | null;
  twilio_phone_number: string | null;
  whatsapp_phone_number_id: string | null;
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
  // Optional dedicated numbers for this org. Omit to use the platform
  // default calling/WhatsApp numbers (see apps/api/schemas/auth.py's
  // ProvisionOrgIn) — can be set later from the org's own settings pages.
  // plivo_phone_number/twilio_phone_number are independent — an org can be
  // given a dedicated number on both providers at once.
  plivo_phone_number?: string;
  twilio_phone_number?: string;
  whatsapp_phone_number_id?: string;
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

export interface OrgPayment {
  id: string;
  provider: string;
  plan_code: string | null;
  plan_name: string | null;
  amount_cents: number;
  status: string;
  period_start: string | null;
  created_at: string;
}

/** GET /billing/orgs/{orgId}/payments → OrgPayment[] (platform-admin only) */
export function useOrgPayments(orgId: string, enabled: boolean) {
  return useQuery<OrgPayment[]>({
    queryKey: ["admin", "orgs", orgId, "payments"],
    queryFn: () => apiFetch<OrgPayment[]>(`/billing/orgs/${orgId}/payments`),
    enabled,
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
  plivo_phone_number?: string | null;
  twilio_phone_number?: string | null;
  whatsapp_phone_number_id?: string | null;
}

/**
 * PATCH /billing/orgs/{orgId} → AdminOrg (platform-admin only). Edits the
 * org's own profile fields only — plan/billing_status stay driven by the
 * checkout/payment flow (see apps/api/schemas/billing.py's OrgUpdateIn).
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
