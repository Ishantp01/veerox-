import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { CallingSettings, CallingSettingsInput, WhatsAppSettings } from "@/lib/types";

/**
 * Read-only WhatsApp/Meta channel config status (masked secrets). Static
 * config — no polling; relies on the default 30s staleTime.
 *
 * GET /admin/settings/whatsapp → WhatsAppSettings
 */
export function useWhatsAppSettings() {
  return useQuery<WhatsAppSettings>({
    queryKey: queryKeys.whatsappSettings(),
    queryFn: () => apiFetch<WhatsAppSettings>("/admin/settings/whatsapp"),
  });
}

/**
 * Read-only Plivo voice channel config status (masked secrets). Static
 * config — no polling; relies on the default 30s staleTime.
 *
 * GET /admin/settings/calling → CallingSettings
 */
export function useCallingSettings() {
  return useQuery<CallingSettings>({
    queryKey: queryKeys.callingSettings(),
    queryFn: () => apiFetch<CallingSettings>("/admin/settings/calling"),
  });
}

/**
 * Set (or, with `preferred_provider: null`, clear back to automatic) this
 * org's preferred voice provider — applied to every outbound call the org
 * places (single admin call, AI callback, campaign dialer, follow-up
 * dispatcher).
 *
 * PUT /admin/settings/calling → CallingSettings
 */
export function useUpdateCallingSettings() {
  const queryClient = useQueryClient();
  return useMutation<CallingSettings, Error, CallingSettingsInput>({
    mutationFn: (body) =>
      apiFetch<CallingSettings>("/admin/settings/calling", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.callingSettings(), data);
    },
  });
}
