import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Script } from "@/lib/types";

/**
 * The calling org's active script — its own override if set, else the
 * platform default. Static config — no polling.
 *
 * GET /admin/script → Script
 */
export function useScript() {
  return useQuery<Script>({
    queryKey: queryKeys.script(),
    queryFn: () => apiFetch<Script>("/admin/script"),
  });
}

/**
 * Set (or, with an empty/undefined string, clear) the org's script override.
 *
 * PUT /admin/script { script } → Script
 */
export function useUpdateScript() {
  const queryClient = useQueryClient();

  return useMutation<Script, Error, string | null>({
    mutationFn: (script) =>
      apiFetch<Script>("/admin/script", {
        method: "PUT",
        body: JSON.stringify({ script }),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.script(), data);
    },
  });
}
