import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { QualificationCriteriaPreset } from "@/lib/types";

/**
 * This org's saved qualification-criteria presets — picked from a dropdown
 * when creating a campaign instead of retyping the bar every time.
 *
 * GET /admin/qualification-criteria-presets → QualificationCriteriaPreset[]
 */
export function useQualificationCriteriaPresets() {
  return useQuery<QualificationCriteriaPreset[]>({
    queryKey: queryKeys.qualificationCriteriaPresets(),
    queryFn: () =>
      apiFetch<QualificationCriteriaPreset[]>("/admin/qualification-criteria-presets"),
  });
}

export interface QualificationCriteriaPresetCreateInput {
  name: string;
  criteria_text: string;
}

/** POST /admin/qualification-criteria-presets → QualificationCriteriaPreset */
export function useCreateQualificationCriteriaPreset() {
  const queryClient = useQueryClient();

  return useMutation<QualificationCriteriaPreset, Error, QualificationCriteriaPresetCreateInput>({
    mutationFn: (body) =>
      apiFetch<QualificationCriteriaPreset>("/admin/qualification-criteria-presets", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.qualificationCriteriaPresets() });
    },
  });
}

export interface QualificationCriteriaPresetUpdateInput {
  id: string;
  name?: string;
  criteria_text?: string;
}

/** PATCH /admin/qualification-criteria-presets/{id} → QualificationCriteriaPreset */
export function useUpdateQualificationCriteriaPreset() {
  const queryClient = useQueryClient();

  return useMutation<QualificationCriteriaPreset, Error, QualificationCriteriaPresetUpdateInput>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<QualificationCriteriaPreset>(`/admin/qualification-criteria-presets/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.qualificationCriteriaPresets() });
    },
  });
}

/** DELETE /admin/qualification-criteria-presets/{id} */
export function useDeleteQualificationCriteriaPreset() {
  const queryClient = useQueryClient();

  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) =>
      apiFetch<{ ok: boolean }>(`/admin/qualification-criteria-presets/${id}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.qualificationCriteriaPresets() });
    },
  });
}
