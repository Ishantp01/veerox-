import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { PlatformSettings } from "@/lib/types";

/**
 * Platform-wide help-desk script + social links (platform-admin only).
 *
 * GET /billing/platform-settings → PlatformSettings
 */
export function usePlatformSettings() {
  return useQuery<PlatformSettings>({
    queryKey: queryKeys.platformSettings(),
    queryFn: () => apiFetch<PlatformSettings>("/billing/platform-settings"),
  });
}

export interface UpdatePlatformSettingsInput {
  help_desk_script?: string | null;
  social_links?: Record<string, string>;
}

/**
 * Partial update — only the keys passed are touched.
 *
 * PATCH /billing/platform-settings → PlatformSettings
 */
export function useUpdatePlatformSettings() {
  const queryClient = useQueryClient();

  return useMutation<PlatformSettings, Error, UpdatePlatformSettingsInput>({
    mutationFn: (body) =>
      apiFetch<PlatformSettings>("/billing/platform-settings", {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.platformSettings(), data);
      queryClient.invalidateQueries({ queryKey: queryKeys.socialLinks() });
    },
  });
}
