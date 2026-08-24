import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface AdminPlan {
  code: string;
  name: string;
  price_cents: number;
  limits: Record<string, number | boolean>;
  is_active: boolean;
  // null = a full subscription plan (updates every resource on purchase).
  // Otherwise one of "max_call_minutes" | "max_whatsapp_messages" |
  // "max_team_members" | "max_campaigns" — a single-resource recharge SKU
  // that only tops up that one resource, leaving the other three untouched.
  resource_type: string | null;
}

const adminPlanKeys = {
  all: () => ["admin", "plans"] as const,
};

// "" (sent as null) = a full subscription plan. Otherwise the recharge SKU
// this plan represents — see AdminPlan.resource_type.
export const PLAN_RESOURCE_TYPE_OPTIONS = [
  { value: "", label: "Full plan (all resources)" },
  { value: "max_call_minutes", label: "Recharge: Call Minutes only" },
  { value: "max_whatsapp_messages", label: "Recharge: WhatsApp Messages only" },
  { value: "max_team_members", label: "Recharge: Team Members only" },
  { value: "max_campaigns", label: "Recharge: Campaigns only" },
] as const;

/** GET /billing/plans → AdminPlan[] (platform-admin only) */
export function useAdminPlans() {
  return useQuery<AdminPlan[]>({
    queryKey: adminPlanKeys.all(),
    queryFn: () => apiFetch<AdminPlan[]>("/billing/plans"),
  });
}

export interface CreatePlanInput {
  code: string;
  name: string;
  price_cents: number;
  limits: Record<string, number | boolean>;
  is_active?: boolean;
  resource_type?: string | null;
}

/** POST /billing/plans → AdminPlan */
export function useCreatePlan() {
  const queryClient = useQueryClient();
  return useMutation<AdminPlan, Error, CreatePlanInput>({
    mutationFn: (body) =>
      apiFetch<AdminPlan>("/billing/plans", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminPlanKeys.all() });
    },
  });
}

export interface UpdatePlanInput {
  code: string;
  name?: string;
  price_cents?: number;
  limits?: Record<string, number | boolean>;
  is_active?: boolean;
  resource_type?: string | null;
}

/** PATCH /billing/plans/{code} → AdminPlan */
export function useUpdatePlan() {
  const queryClient = useQueryClient();
  return useMutation<AdminPlan, Error, UpdatePlanInput>({
    mutationFn: ({ code, ...body }) =>
      apiFetch<AdminPlan>(`/billing/plans/${code}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminPlanKeys.all() });
    },
  });
}

/** DELETE /billing/plans/{code} */
export function useDeletePlan() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (code) => apiFetch<void>(`/billing/plans/${code}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminPlanKeys.all() });
    },
  });
}
