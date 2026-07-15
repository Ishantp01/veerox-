import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { Conversation, Message } from "@/lib/types";

export interface ConversationFilters {
  channel?: "voice" | "whatsapp";
  limit?: number;
  offset?: number;
}

function buildConversationsPath(filters?: ConversationFilters): string {
  const params = new URLSearchParams();
  if (filters?.channel) params.set("channel", filters.channel);
  if (filters?.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters?.offset !== undefined) params.set("offset", String(filters.offset));
  const qs = params.toString();
  return qs ? `/admin/conversations?${qs}` : "/admin/conversations";
}

/**
 * Conversation list, newest first. Polls every 10s (POLL.conversationList).
 * Optional `channel` filters server-side; `limit`/`offset` map straight onto
 * the backend query params.
 *
 * GET /admin/conversations → Conversation[]
 */
export function useConversations(filters?: ConversationFilters) {
  return useQuery<Conversation[]>({
    queryKey: queryKeys.conversations(filters && { channel: filters.channel }),
    queryFn: () => apiFetch<Conversation[]>(buildConversationsPath(filters)),
    refetchInterval: POLL.conversationList,
  });
}

export interface ConversationMessagesOptions {
  /** When true the transcript polls every 5s; set false once ended_at is set. */
  isLive?: boolean;
}

/**
 * Messages for a single conversation, oldest first. While `isLive` is true the
 * transcript polls every 5s (POLL.liveConversation); once the conversation has
 * ended pass isLive=false to stop polling. Disabled until `id` is provided.
 *
 * GET /admin/conversations/{id}/messages → Message[]
 */
export function useConversationMessages(
  id: string | undefined | null,
  opts?: ConversationMessagesOptions
) {
  const isLive = opts?.isLive ?? false;
  return useQuery<Message[]>({
    queryKey: queryKeys.conversationMessages(id ?? ""),
    queryFn: () => apiFetch<Message[]>(`/admin/conversations/${id}/messages`),
    enabled: Boolean(id),
    refetchInterval: isLive ? POLL.liveConversation : false,
  });
}
