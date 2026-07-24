import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  OutboundCallResponse,
  OutboundWhatsAppResponse,
} from "@/lib/types";

export interface OutboundCallInput {
  to_phone: string;
}

/**
 * Place an outbound voice call.
 *
 * POST /admin/outbound/call { to_phone } → OutboundCallResponse
 *
 * A successful dial creates a conversation server-side, so the conversation
 * list and dashboard stats are invalidated to surface it.
 */
export function useOutboundCall() {
  const queryClient = useQueryClient();

  return useMutation<OutboundCallResponse, Error, OutboundCallInput>({
    mutationFn: ({ to_phone }: OutboundCallInput) =>
      apiFetch<OutboundCallResponse>("/admin/outbound/call", {
        method: "POST",
        body: JSON.stringify({ to_phone }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations() });
      queryClient.invalidateQueries({ queryKey: queryKeys.stats() });
    },
  });
}

export interface OutboundWhatsAppInput {
  phone: string;
  /** Free-form body. Deliverable only inside the 24-hour customer-service window. */
  text?: string;
  /** Approved template name — required to message a user outside the window. */
  template_name?: string;
  template_lang?: string;
  /** Ordered values for the template body {{1}}, {{2}} ... placeholders. */
  template_params?: string[];
}

/**
 * Send an outbound WhatsApp message — either free-form text or an approved
 * template (the only way to reach a user outside the 24-hour window).
 *
 * POST /admin/outbound/whatsapp { phone, text | template_name/... } → OutboundWhatsAppResponse
 *
 * The backend persists the assistant turn into a (possibly new) WhatsApp
 * conversation but does not return its id, so we invalidate the conversation
 * list + stats. Pages that have a known conversation id open should additionally
 * invalidate queryKeys.conversationMessages(id) themselves — see report.
 */
export function useOutboundWhatsApp() {
  const queryClient = useQueryClient();

  return useMutation<OutboundWhatsAppResponse, Error, OutboundWhatsAppInput>({
    mutationFn: (input: OutboundWhatsAppInput) =>
      apiFetch<OutboundWhatsAppResponse>("/admin/outbound/whatsapp", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations() });
      queryClient.invalidateQueries({ queryKey: queryKeys.stats() });
    },
  });
}
