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
}

export interface ProvisionOrgResult {
  org_id: string;
  account_user_id: string;
  email: string;
  // Shown exactly once — hand both this and the email to the organization
  // so its admin can log in (see apps/api/routers/auth.py's provision_org).
  login_token: string;
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
