import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { ScriptLibraryItem } from "@/lib/types";

export type ScriptChannel = "voice" | "whatsapp";

/**
 * This org's AI script library for one channel — pick one per campaign, or
 * leave the is_default one (for that channel) as the fallback every
 * campaign without its own pick uses.
 *
 * GET /admin/scripts?channel= → ScriptLibraryItem[]
 */
export function useScripts(channel: ScriptChannel = "voice") {
  return useQuery<ScriptLibraryItem[]>({
    queryKey: queryKeys.scripts(channel),
    queryFn: () => apiFetch<ScriptLibraryItem[]>(`/admin/scripts?channel=${channel}`),
  });
}

export interface ScriptCreateInput {
  name: string;
  content: string;
  channel?: ScriptChannel;
  is_default?: boolean;
  /** Pair this script with one of the org's WhatsApp numbers (label only). */
  phone_number_id?: string | null;
}

/** POST /admin/scripts → ScriptLibraryItem */
export function useCreateScript() {
  const queryClient = useQueryClient();

  return useMutation<ScriptLibraryItem, Error, ScriptCreateInput>({
    mutationFn: (body) =>
      apiFetch<ScriptLibraryItem>("/admin/scripts", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scripts(data.channel) });
    },
  });
}

export interface ScriptLibraryUpdateInput {
  id: string;
  name?: string;
  content?: string;
  /** Pair this script with one of the org's WhatsApp numbers, or null to unpair it. */
  phone_number_id?: string | null;
}

/** PATCH /admin/scripts/{id} → ScriptLibraryItem */
export function useUpdateScriptLibraryItem() {
  const queryClient = useQueryClient();

  return useMutation<ScriptLibraryItem, Error, ScriptLibraryUpdateInput>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<ScriptLibraryItem>(`/admin/scripts/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scripts(data.channel) });
    },
  });
}

/** POST /admin/scripts/{id}/set-default → ScriptLibraryItem */
export function useSetDefaultScript() {
  const queryClient = useQueryClient();

  return useMutation<ScriptLibraryItem, Error, string>({
    mutationFn: (id) =>
      apiFetch<ScriptLibraryItem>(`/admin/scripts/${id}/set-default`, { method: "POST" }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scripts(data.channel) });
    },
  });
}

/** DELETE /admin/scripts/{id} */
export function useDeleteScript() {
  const queryClient = useQueryClient();

  return useMutation<{ ok: boolean }, Error, { id: string; channel: ScriptChannel }>({
    mutationFn: ({ id }) => apiFetch<{ ok: boolean }>(`/admin/scripts/${id}`, { method: "DELETE" }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scripts(variables.channel) });
    },
  });
}
