import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { LeadStatusPreset } from "@/lib/types";

/**
 * This org's custom lead pipeline stages, on top of the built-in
 * new/contacted/qualified/converted/lost — offered alongside them in the
 * lead status dropdown (status-badge.tsx's LEAD_STATUS_OPTIONS).
 *
 * GET /admin/lead-status-presets → LeadStatusPreset[]
 */
export function useLeadStatusPresets() {
  return useQuery<LeadStatusPreset[]>({
    queryKey: queryKeys.leadStatusPresets(),
    queryFn: () => apiFetch<LeadStatusPreset[]>("/admin/lead-status-presets"),
  });
}

/** POST /admin/lead-status-presets → LeadStatusPreset */
export function useCreateLeadStatusPreset() {
  const queryClient = useQueryClient();

  return useMutation<LeadStatusPreset, Error, { name: string }>({
    mutationFn: (body) =>
      apiFetch<LeadStatusPreset>("/admin/lead-status-presets", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.leadStatusPresets() });
    },
  });
}

/** DELETE /admin/lead-status-presets/{id} */
export function useDeleteLeadStatusPreset() {
  const queryClient = useQueryClient();

  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) =>
      apiFetch<{ ok: boolean }>(`/admin/lead-status-presets/${id}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.leadStatusPresets() });
    },
  });
}
