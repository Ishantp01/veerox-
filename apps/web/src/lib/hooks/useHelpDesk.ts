import { useMutation } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { HelpDeskMessage } from "@/lib/types";

export interface HelpDeskChatInput {
  message: string;
  history: HelpDeskMessage[];
}

export interface HelpDeskChatResult {
  reply: string;
}

/**
 * One turn of the help-desk chatbot — stateless server-side, the widget
 * keeps the conversation in local state and resends recent history.
 *
 * POST /helpdesk/chat → { reply }
 */
export function useHelpDeskChat() {
  return useMutation<HelpDeskChatResult, Error, HelpDeskChatInput>({
    mutationFn: (body) =>
      apiFetch<HelpDeskChatResult>("/helpdesk/chat", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}
