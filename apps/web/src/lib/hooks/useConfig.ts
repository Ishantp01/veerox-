import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { CallingSettings, WhatsAppSettings } from "@/lib/types";

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
