import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface AdminPlan {
  code: string;
  name: string;
  price_cents: number;
  limits: Record<string, number | boolean>;
  is_active: boolean;
}

const adminPlanKeys = {
  all: () => ["admin", "plans"] as const,
};

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
