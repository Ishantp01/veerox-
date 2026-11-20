import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { ChatLead, ChatThread } from "@/lib/types";

export interface ChatLeadFilters {
  channel?: "voice" | "whatsapp";
  search?: string;
}

/**
 * Chat list: one row per customer (not per conversation), newest activity
 * first, with last-message preview and this staff member's unread count.
 *
 * GET /admin/chat/leads → ChatLead[]
 */
export function useChatLeads(filters?: ChatLeadFilters) {
  return useQuery<ChatLead[]>({
    queryKey: queryKeys.chatLeads(filters),
    queryFn: () => {
      const params = new URLSearchParams({ limit: "200" });
      if (filters?.channel) params.set("channel", filters.channel);
      if (filters?.search) params.set("search", filters.search);
      return apiFetch<ChatLead[]>(`/admin/chat/leads?${params.toString()}`);
    },
    refetchInterval: POLL.conversationList,
    placeholderData: keepPreviousData,
  });
}

/**
 * One customer's whole thread — every WhatsApp message and voice call across
 * all of their conversation rows, oldest → newest. Polls so new messages land
 * in place. `limit` grows when the user asks for earlier history.
 *
 * GET /admin/chat/leads/{userId} → ChatThread
 */
export function useChatThread(userId: string | null | undefined, limit: number) {
  return useQuery<ChatThread>({
    queryKey: queryKeys.chatThread(userId ?? "", limit),
    queryFn: () => apiFetch<ChatThread>(`/admin/chat/leads/${userId}?limit=${limit}`),
    enabled: Boolean(userId),
    refetchInterval: POLL.chatThread,
    placeholderData: keepPreviousData,
  });
}

/** Clears the unread badge for the signed-in staff member.
 *
 * POST /admin/chat/leads/{userId}/read → 204 */
export function useMarkChatRead() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (userId) =>
      apiFetch<void>(`/admin/chat/leads/${userId}/read`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["chat", "leads"] });
    },
  });
}
