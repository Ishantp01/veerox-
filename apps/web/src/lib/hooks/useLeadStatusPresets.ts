import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import { LEAD_STATUS_LABELS, LEAD_STATUS_OPTIONS } from "@/components/leads/status-badge";
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

/**
 * Every status a lead can have in this org — the built-ins plus its custom
 * ones — as {value,label} options, plus `labelFor` for showing any status
 * string (custom names are their own label). Use this for any status
 * dropdown/label outside the leads pages so custom statuses appear everywhere.
 */
export function useLeadStatusOptions() {
  const { data } = useLeadStatusPresets();
  const options = [
    ...LEAD_STATUS_OPTIONS.map((s) => ({ value: s as string, label: LEAD_STATUS_LABELS[s] })),
    ...(data ?? []).map((p) => ({ value: p.name, label: p.name })),
  ];
  const labelFor = (status: string) =>
    (LEAD_STATUS_LABELS as Record<string, string>)[status] ?? status;
  return { options, labelFor };
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
