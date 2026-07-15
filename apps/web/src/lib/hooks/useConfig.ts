import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { CallingSettings, Prompts, Tool, WhatsAppSettings } from "@/lib/types";

/**
 * Active system prompts (base + per-channel appends). Static config — no
 * polling; relies on the default 30s staleTime from makeQueryClient.
 *
 * GET /admin/prompts → Prompts
 */
export function usePrompts() {
  return useQuery<Prompts>({
    queryKey: queryKeys.prompts(),
    queryFn: () => apiFetch<Prompts>("/admin/prompts"),
  });
}

/**
 * Registered tool schemas. Static config — no polling.
 *
 * GET /admin/tools → Tool[]
 */
export function useTools() {
  return useQuery<Tool[]>({
    queryKey: queryKeys.tools(),
    queryFn: () => apiFetch<Tool[]>("/admin/tools"),
  });
}

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
