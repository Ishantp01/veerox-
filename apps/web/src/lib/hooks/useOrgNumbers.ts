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
 * Set (or, with an empty string, clear) the org's WhatsApp number, and/or
 * replace its full set of dedicated calling numbers. Only the keys passed
 * are touched — omitting `phone_numbers` entirely leaves it as-is.
 *
 * PUT /admin/org-numbers { whatsapp_phone_number_id?, phone_numbers? } → OrgNumbers
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
