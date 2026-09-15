import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { OrgNumbers } from "@/lib/types";

/**
 * The org's dedicated WhatsApp/calling numbers — inbound messages/calls on
 * these are attributed to this org instead of the platform default.
 *
 * GET /admin/org-numbers → OrgNumbers
 */
export function useOrgNumbers() {
  return useQuery<OrgNumbers>({
    queryKey: queryKeys.orgNumbers(),
    queryFn: () => apiFetch<OrgNumbers>("/admin/org-numbers"),
  });
}

/**
 * Replace the org's full set of dedicated numbers — Plivo, Twilio, and
 * WhatsApp alike. `phone_numbers` omitted leaves it as-is; present replaces
 * the whole set, so callers must resubmit every provider's numbers
 * together, not just the ones changing.
 *
 * PUT /admin/org-numbers { phone_numbers? } → OrgNumbers
 */
export function useUpdateOrgNumbers() {
  const queryClient = useQueryClient();

  return useMutation<OrgNumbers, Error, Partial<OrgNumbers>>({
    mutationFn: (fields) =>
      apiFetch<OrgNumbers>("/admin/org-numbers", {
        method: "PUT",
        body: JSON.stringify(fields),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.orgNumbers(), data);
    },
  });
}
