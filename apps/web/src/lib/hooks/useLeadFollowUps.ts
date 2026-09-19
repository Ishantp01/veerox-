import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { LeadUpdateInput } from "./useLeads";

/** One scheduled follow-up slot (1, 2 or 3) on a lead. */
export interface LeadFollowUp {
  lead_id: string;
  slot: 1 | 2 | 3;
  lead_name: string | null;
  lead_phone: string | null;
  follow_up_at: string;
  note: string | null;
  status: string | null;
  channel: string | null;
  conversation_id: string | null;
  claimed_by_account_user_id: string | null;
  claimed_by_name: string | null;
}

export function followUpKey(f: Pick<LeadFollowUp, "lead_id" | "slot">): string {
  return `${f.lead_id}:${f.slot}`;
}

/**
 * Scheduled lead follow-ups, soonest first. `due` restricts to the ones whose
 * time has already arrived — that variant feeds the reminder popup.
 *
 * GET /lead-follow-ups[?due=true]
 */
export function useLeadFollowUps(opts?: { due?: boolean }) {
  const due = opts?.due ?? false;
  return useQuery<LeadFollowUp[]>({
    queryKey: queryKeys.leadFollowUps({ due }),
    queryFn: () => apiFetch<LeadFollowUp[]>(`/lead-follow-ups${due ? "?due=true" : ""}`),
    refetchInterval: due ? POLL.leadFollowUpsDue : POLL.leadFollowUps,
  });
}

/** PATCH body that sets (or clears, with `at: null`) one slot's date + note. */
export function slotPatch(
  slot: 1 | 2 | 3,
  values: { at?: string | null; note?: string | null },
): Omit<LeadUpdateInput, "id"> {
  const prefix = slot === 1 ? "follow_up" : `follow_up_${slot}`;
  const patch: Record<string, string | null> = {};
  if (values.at !== undefined) patch[`${prefix}_at`] = values.at;
  if (values.note !== undefined) patch[`${prefix}_note`] = values.note;
  return patch;
}

/**
 * Reschedule, re-note or complete (`at: null`) a follow-up. Goes through the
 * normal lead PATCH so member scoping and validation stay in one place.
 */
export function useUpdateLeadFollowUp() {
  const queryClient = useQueryClient();
  return useMutation<
    unknown,
    Error,
    { leadId: string; slot: 1 | 2 | 3; at?: string | null; note?: string | null }
  >({
    mutationFn: ({ leadId, slot, ...values }) =>
      apiFetch(`/admin/leads/${leadId}`, {
        method: "PATCH",
        body: JSON.stringify(slotPatch(slot, values)),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["lead-follow-ups"] });
      queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
  });
}
