import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { FollowUpRule, FollowUpTask, FollowUpTaskStatus } from "@/lib/types";

/** GET /follow-up-rules → FollowUpRule[] */
export function useFollowUpRules() {
  return useQuery<FollowUpRule[]>({
    queryKey: queryKeys.followUpRules(),
    queryFn: () => apiFetch<FollowUpRule[]>("/follow-up-rules"),
  });
}

export interface FollowUpRuleCreateInput {
  name: string;
  trigger_config: { status: string; delay_hours: number };
  channel?: string;
  message_template?: string;
  template_name?: string;
  template_language?: string;
  template_params?: string[];
}

/** POST /follow-up-rules → FollowUpRule */
export function useCreateFollowUpRule() {
  const queryClient = useQueryClient();

  return useMutation<FollowUpRule, Error, FollowUpRuleCreateInput>({
    mutationFn: (body) =>
      apiFetch<FollowUpRule>("/follow-up-rules", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.followUpRules() });
    },
  });
}

/** PATCH /follow-up-rules/{id} → FollowUpRule */
export function useUpdateFollowUpRule() {
  const queryClient = useQueryClient();

  return useMutation<FollowUpRule, Error, { id: string; active: boolean }>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<FollowUpRule>(`/follow-up-rules/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.followUpRules() });
    },
  });
}

export interface FollowUpTaskFilters {
  status?: FollowUpTaskStatus;
}

/** GET /follow-up-tasks → FollowUpTask[] */
export function useFollowUpTasks(filters?: FollowUpTaskFilters) {
  const qs = filters?.status ? `?status=${filters.status}` : "";
  return useQuery<FollowUpTask[]>({
    queryKey: queryKeys.followUpTasks(filters),
    queryFn: () => apiFetch<FollowUpTask[]>(`/follow-up-tasks${qs}`),
  });
}

/** POST /follow-up-tasks/{id}/cancel → FollowUpTask */
export function useCancelFollowUpTask() {
  const queryClient = useQueryClient();

  return useMutation<FollowUpTask, Error, string>({
    mutationFn: (id) =>
      apiFetch<FollowUpTask>(`/follow-up-tasks/${id}/cancel`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["follow-up-tasks"] });
    },
  });
}
