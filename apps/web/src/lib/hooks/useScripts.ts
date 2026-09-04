import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { ScriptLibraryItem } from "@/lib/types";

/**
 * This org's voice-only AI-calling script library — pick one per campaign,
 * or leave the is_default one as the fallback every campaign without its
 * own pick uses. Distinct from useScript.ts's singular WhatsApp override.
 *
 * GET /admin/scripts → ScriptLibraryItem[]
 */
export function useScripts() {
  return useQuery<ScriptLibraryItem[]>({
    queryKey: queryKeys.scripts(),
    queryFn: () => apiFetch<ScriptLibraryItem[]>("/admin/scripts"),
  });
}

export interface ScriptCreateInput {
  name: string;
  content: string;
  is_default?: boolean;
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scripts() });
    },
  });
}

export interface ScriptLibraryUpdateInput {
  id: string;
  name?: string;
  content?: string;
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scripts() });
    },
  });
}

/** POST /admin/scripts/{id}/set-default → ScriptLibraryItem */
export function useSetDefaultScript() {
  const queryClient = useQueryClient();

  return useMutation<ScriptLibraryItem, Error, string>({
    mutationFn: (id) =>
      apiFetch<ScriptLibraryItem>(`/admin/scripts/${id}/set-default`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scripts() });
    },
  });
}

/** DELETE /admin/scripts/{id} */
export function useDeleteScript() {
  const queryClient = useQueryClient();

  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) => apiFetch<{ ok: boolean }>(`/admin/scripts/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scripts() });
    },
  });
}
