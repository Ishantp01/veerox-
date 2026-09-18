import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { Conversation, Message } from "@/lib/types";

export interface ConversationFilters {
  channel?: "voice" | "whatsapp";
  /** Substring match against the contact's name/phone or tags — the
   * dashboard's unified conversation search box. */
  search?: string;
  limit?: number;
  offset?: number;
}

function buildConversationsPath(filters?: ConversationFilters): string {
  const params = new URLSearchParams();
  if (filters?.channel) params.set("channel", filters.channel);
  if (filters?.search) params.set("search", filters.search);
  if (filters?.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters?.offset !== undefined) params.set("offset", String(filters.offset));
  const qs = params.toString();
  return qs ? `/admin/conversations?${qs}` : "/admin/conversations";
}

/**
 * Look up the most recent conversation with a contact by their exact phone
 * number — for contexts (like a campaign target row) that only have the
 * phone, not a conversation_id. Returns null if they've never messaged/been
 * called, or that conversation isn't visible to this caller.
 *
 * GET /admin/conversations?phone=...&limit=1 → Conversation | null
 */
export async function findConversationByPhone(phone: string): Promise<Conversation | null> {
  const rows = await apiFetch<Conversation[]>(
    `/admin/conversations?phone=${encodeURIComponent(phone)}&limit=1`
  );
  return rows[0] ?? null;
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
    // Cache key includes limit/offset (not just channel) — otherwise every
    // page of results would collide on the same cache entry and paging
    // would just keep re-showing page 1.
    queryKey: queryKeys.conversations(filters),
    queryFn: () => apiFetch<Conversation[]>(buildConversationsPath(filters)),
    refetchInterval: POLL.conversationList,
    // Keep the previous page's rows on screen while the next page loads —
    // otherwise every Prev/Next click flashes the loading skeleton because
    // a new offset means a fresh (empty) cache entry.
    placeholderData: keepPreviousData,
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

/**
 * Update a conversation's tags — currently the only editable field on one.
 *
 * PATCH /admin/conversations/{id} → Conversation
 */
export function useUpdateConversation() {
  const queryClient = useQueryClient();

  return useMutation<Conversation, Error, { id: string; tags: string[] | null }>({
    mutationFn: ({ id, tags }) =>
      apiFetch<Conversation>(`/admin/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ tags }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

/**
 * Generate (or regenerate) an AI summary of a conversation's transcript.
 *
 * POST /admin/conversations/{id}/summarize → Conversation
 */
export function useSummarizeConversation() {
  const queryClient = useQueryClient();

  return useMutation<Conversation, Error, string>({
    mutationFn: (id) =>
      apiFetch<Conversation>(`/admin/conversations/${id}/summarize`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}
