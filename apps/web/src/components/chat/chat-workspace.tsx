"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  MessagesSquare,
  Phone,
  Search,
  UserPlus,
} from "lucide-react";

import { ChannelBadge } from "@/components/conversations/channel-badge";
import { AddContactDialog } from "@/components/chat/add-contact-dialog";
import { Button, EmptyState, Select, Skeleton } from "@/components/ui";
import { formatDuration, formatPhone } from "@/lib/format";
import { useChatLeads, useChatThread, useMarkChatRead, useOpenContactChat } from "@/lib/hooks";
import type { ChatSort } from "@/lib/hooks/useChat";
import type { ChatConversationInfo, ChatLead, ChatMessage, ChatThread } from "@/lib/types";
import { cn } from "@/lib/utils";

const PAGE = 300;

type ChannelFilter = "" | "voice" | "whatsapp";

// ---------- time helpers ----------

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function formatClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** "Today · Wednesday, 19 Nov 2026" / "Yesterday · Tuesday, 18 Nov 2026" / "Monday, 17 Nov 2026" */
function formatDayLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const full = d.toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  const now = new Date();
  if (dayKey(d) === dayKey(now)) return `Today · ${full}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (dayKey(d) === dayKey(yesterday)) return `Yesterday · ${full}`;
  return full;
}

/** Compact list-row time: clock today, "Yesterday", weekday this week, else date. */
function formatListTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  if (dayKey(d) === dayKey(now)) return formatClock(iso);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (dayKey(d) === dayKey(yesterday)) return "Yesterday";
  const days = (now.getTime() - d.getTime()) / 86_400_000;
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "2-digit" });
}

function displayName(lead: { name: string | null; phone: string }): string {
  return lead.name?.trim() || formatPhone(lead.phone);
}

// ---------- timeline model ----------

type Entry =
  | { kind: "message"; key: string; at: string; message: ChatMessage }
  | {
      kind: "call";
      key: string;
      at: string;
      call: ChatConversationInfo;
      transcript: ChatMessage[];
    };

/** Voice conversations become one call card holding their transcript lines;
 * WhatsApp messages stay individual bubbles. Everything is ordered by time. */
function buildEntries(thread: ChatThread): Entry[] {
  const calls = new Map<string, ChatConversationInfo>();
  for (const c of thread.conversations) if (c.channel === "voice") calls.set(c.id, c);

  const transcripts = new Map<string, ChatMessage[]>();
  const entries: Entry[] = [];
  for (const m of thread.messages) {
    if (calls.has(m.conversation_id)) {
      const lines = transcripts.get(m.conversation_id) ?? [];
      lines.push(m);
      transcripts.set(m.conversation_id, lines);
    } else {
      entries.push({ kind: "message", key: m.id, at: m.created_at, message: m });
    }
  }
  // Only show a call card if its transcript is in this page, or the call
  // itself falls inside the loaded window (so a short page doesn't drop calls
  // that simply have no transcript).
  const windowStart = thread.messages[0]?.created_at;
  for (const [id, call] of calls) {
    const lines = transcripts.get(id) ?? [];
    const inWindow = !thread.has_more || !windowStart || call.started_at >= windowStart;
    if (lines.length === 0 && !inWindow) continue;
    entries.push({ kind: "call", key: `call-${id}`, at: call.started_at, call, transcript: lines });
  }
  entries.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
  return entries;
}

// ---------- pieces ----------

function LeadRow({
  lead,
  active,
  onSelect,
}: {
  lead: ChatLead;
  active: boolean;
  onSelect: () => void;
}) {
  const unread = lead.unread_count > 0;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "flex w-full items-start gap-3 border-b border-slate-100 px-4 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500 dark:border-slate-800",
        active
          ? "bg-primary-50 dark:bg-primary-500/10"
          : "hover:bg-slate-50 dark:hover:bg-slate-800/50"
      )}
    >
      <span
        aria-hidden
        className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-200 text-sm font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-200"
      >
        {lead.name?.trim() ? lead.name.trim().slice(0, 1).toUpperCase() : <Phone size={16} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-2">
          <span
            className={cn(
              "truncate text-sm text-slate-900 dark:text-slate-100",
              unread ? "font-bold" : "font-medium"
            )}
          >
            {displayName(lead)}
          </span>
          <span
            className={cn(
              "shrink-0 text-[11px]",
              unread ? "font-semibold text-primary-600" : "text-slate-400"
            )}
          >
            {formatListTime(lead.last_activity_at)}
          </span>
        </span>
        <span className="mt-0.5 flex items-center justify-between gap-2">
          <span className="truncate text-xs text-slate-500 dark:text-slate-400">
            {lead.last_message_preview
              ? `${lead.last_message_role === "user" ? "" : "You: "}${lead.last_message_preview}`
              : "No messages yet"}
          </span>
          {unread && (
            <span
              className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-primary-600 px-1.5 text-[11px] font-bold text-white"
              aria-label={`${lead.unread_count} unread`}
            >
              {lead.unread_count > 99 ? "99+" : lead.unread_count}
            </span>
          )}
        </span>
        {lead.name && (
          <span className="mt-0.5 block truncate text-[11px] text-slate-400">
            {formatPhone(lead.phone)}
          </span>
        )}
      </span>
    </button>
  );
}

function DayDivider({ iso }: { iso: string }) {
  return (
    <div className="sticky top-2 z-10 my-4 flex justify-center">
      <span className="rounded-full border border-slate-200 bg-white/95 px-3 py-1 text-[11px] font-medium text-slate-500 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/95 dark:text-slate-400">
        {formatDayLabel(iso)}
      </span>
    </div>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const inbound = message.role === "user";
  return (
    <div className={cn("flex flex-col gap-1", inbound ? "items-start" : "items-end")}>
      <div
        className={cn(
          "max-w-[80%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm",
          inbound
            ? "rounded-bl-md border border-slate-200 bg-white text-slate-800 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            : "rounded-br-md bg-gradient-to-br from-primary-500 to-primary-600 text-white shadow-sm"
        )}
      >
        {message.content}
      </div>
      <div className="flex items-center gap-1.5 px-1 text-[11px] text-slate-400">
        <ChannelBadge channel="whatsapp" />
        <span>{inbound ? "Customer" : "AI"}</span>
        <span aria-hidden>·</span>
        <time dateTime={message.created_at}>{formatClock(message.created_at)}</time>
      </div>
    </div>
  );
}

function CallCard({ call, transcript }: { call: ChatConversationInfo; transcript: ChatMessage[] }) {
  const [open, setOpen] = useState(false);
  const duration = call.recording_duration_secs;
  return (
    <div className="mx-auto w-full max-w-[85%] rounded-2xl border border-purple-200 bg-purple-50/60 p-3 text-sm dark:border-purple-500/20 dark:bg-purple-500/10">
      <div className="flex flex-wrap items-center gap-2">
        <Phone size={15} className="text-purple-600" aria-hidden />
        <span className="font-semibold text-slate-800 dark:text-slate-100">Voice call</span>
        <ChannelBadge channel="voice" />
        <span className="text-xs text-slate-500">
          <time dateTime={call.started_at}>{formatClock(call.started_at)}</time>
          {call.ended_at && <> – <time dateTime={call.ended_at}>{formatClock(call.ended_at)}</time></>}
          {duration ? ` · ${formatDuration(duration)}` : ""}
        </span>
      </div>
      {call.summary && (
        <p className="mt-2 text-slate-700 dark:text-slate-300">
          <span className="font-medium">Summary: </span>
          {call.summary}
        </p>
      )}
      {call.recording_url && (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <audio controls preload="none" src={call.recording_url} className="mt-2 h-9 w-full" />
      )}
      {transcript.length > 0 ? (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="mt-2 inline-flex items-center gap-1 rounded text-xs font-medium text-purple-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:text-purple-300"
          >
            {open ? <ChevronUp size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
            {open ? "Hide transcript" : `Show transcript (${transcript.length})`}
          </button>
          {open && (
            <ol className="mt-2 space-y-1.5 border-t border-purple-200/70 pt-2 dark:border-purple-500/20">
              {transcript.map((m) => (
                <li key={m.id} className="flex gap-2 text-[13px]">
                  <time
                    dateTime={m.created_at}
                    className="w-16 shrink-0 pt-0.5 text-[11px] text-slate-400"
                  >
                    {formatClock(m.created_at)}
                  </time>
                  <span>
                    <span className="font-semibold text-slate-600 dark:text-slate-300">
                      {m.role === "user" ? "Customer" : "AI"}:{" "}
                    </span>
                    <span className="text-slate-700 dark:text-slate-200">{m.content}</span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </>
      ) : (
        <p className="mt-2 text-xs text-slate-400">No transcript for this call.</p>
      )}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 break-words text-sm text-slate-800 dark:text-slate-100">{value}</dd>
    </div>
  );
}

function ContactPanel({ thread }: { thread: ChatThread }) {
  const { contact, lead } = thread;
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const calls = thread.conversations.filter((c) => c.channel === "voice").length;
  const chats = thread.conversations.length - calls;
  return (
    <aside
      aria-label="Contact details"
      className="hidden w-72 shrink-0 overflow-y-auto border-l border-slate-200 bg-white p-5 xl:block dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="flex flex-col items-center text-center">
        <span
          aria-hidden
          className="flex h-16 w-16 items-center justify-center rounded-full bg-slate-200 text-xl font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-200"
        >
          {contact.name?.trim() ? contact.name.trim().slice(0, 1).toUpperCase() : <Phone size={24} />}
        </span>
        <h2 className="mt-3 text-base font-semibold text-slate-900 dark:text-slate-100">
          {displayName(contact)}
        </h2>
        <p className="text-sm text-slate-500">{formatPhone(contact.phone)}</p>
        {contact.in_contacts ? (
          <span className="mt-3 rounded-full bg-green-50 px-3 py-1 text-[11px] font-medium text-green-700 dark:bg-green-500/10 dark:text-green-300">
            Saved in contacts
          </span>
        ) : (
          <Button
            type="button"
            size="sm"
            className="mt-3"
            onClick={() => setAdding(true)}
          >
            <UserPlus size={14} aria-hidden /> Add to contacts
          </Button>
        )}
        {adding && (
          <AddContactDialog
            open
            onOpenChange={setAdding}
            phone={contact.phone}
            initialName={contact.name}
            onCreated={() => queryClient.invalidateQueries({ queryKey: ["chat"] })}
          />
        )}
      </div>
      <dl className="mt-6 space-y-4">
        <DetailRow label="Status" value={lead?.status} />
        <DetailRow label="Qualification" value={lead?.qualification_status} />
        <DetailRow label="Intent" value={lead?.intent} />
        <DetailRow label="Phone" value={formatPhone(contact.phone)} />
        <DetailRow label="Email" value={lead?.email ?? contact.email} />
        <DetailRow label="Company" value={lead?.company ?? contact.company} />
        <DetailRow label="Assigned to" value={lead?.staff_name} />
        <DetailRow
          label="Next follow-up"
          value={lead?.follow_up_at ? new Date(lead.follow_up_at).toLocaleString() : null}
        />
        <DetailRow
          label="Last interaction"
          value={
            contact.last_interaction_at
              ? new Date(contact.last_interaction_at).toLocaleString(undefined, {
                  day: "numeric",
                  month: "short",
                  year: "numeric",
                  hour: "numeric",
                  minute: "2-digit",
                })
              : null
          }
        />
        <DetailRow
          label="First contact"
          value={new Date(contact.first_seen_at).toLocaleDateString(undefined, {
            day: "numeric",
            month: "short",
            year: "numeric",
          })}
        />
        <DetailRow label="Interactions" value={`${chats} WhatsApp · ${calls} calls`} />
      </dl>
      {(lead?.tags.length || contact.tags.length) > 0 && (
        <div className="mt-5 flex flex-wrap gap-1.5">
          {Array.from(new Set([...(lead?.tags ?? []), ...contact.tags])).map((t) => (
            <span
              key={t}
              className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </aside>
  );
}

function ThreadSkeleton() {
  return (
    <div className="flex flex-1 flex-col gap-4 p-6">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className={i % 2 ? "flex justify-end" : "flex justify-start"}>
          <Skeleton className="h-12 w-64 rounded-2xl" />
        </div>
      ))}
    </div>
  );
}

function ConversationPane({
  userId,
  channel,
  onBack,
}: {
  userId: string;
  channel?: "voice" | "whatsapp";
  onBack: () => void;
}) {
  const [limit, setLimit] = useState(PAGE);
  const thread = useChatThread(userId, limit, channel);
  const markRead = useMarkChatRead();
  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const lastMarked = useRef<string>("");

  // New customer selected → reset history window and jump to the latest message.
  useEffect(() => {
    setLimit(PAGE);
    stickToBottom.current = true;
  }, [userId]);

  const data = thread.data?.contact.user_id === userId ? thread.data : undefined;
  const entries = useMemo(() => (data ? buildEntries(data) : []), [data]);
  const newestId = data?.messages[data.messages.length - 1]?.id ?? "none";

  // Keep the newest item in view, unless the reader scrolled up to read history.
  useEffect(() => {
    const el = scroller.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [entries.length, newestId, userId]);

  // Opening a thread (or a new message landing while it's open) clears unread.
  useEffect(() => {
    if (!data) return;
    const marker = `${userId}:${newestId}`;
    if (lastMarked.current === marker) return;
    lastMarked.current = marker;
    markRead.mutate(userId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, userId, newestId]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  let previousDay = "";
  return (
    <div className="flex min-w-0 flex-1">
      <section aria-label="Conversation" className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
          <button
            type="button"
            onClick={onBack}
            aria-label="Back to chats"
            className="rounded-md p-1 text-slate-500 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 lg:hidden"
          >
            <ArrowLeft size={18} aria-hidden />
          </button>
          {data ? (
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                {displayName(data.contact)}
              </h2>
              <p className="truncate text-xs text-slate-500">{formatPhone(data.contact.phone)}</p>
            </div>
          ) : (
            <Skeleton className="h-8 w-40" />
          )}
        </header>

        {thread.isError ? (
          <div className="p-6 text-sm text-red-600">Couldn&apos;t load this chat.</div>
        ) : !data ? (
          <ThreadSkeleton />
        ) : (
          <div
            ref={scroller}
            onScroll={onScroll}
            className="flex-1 space-y-3 overflow-y-auto bg-slate-50 px-4 py-4 dark:bg-slate-950"
          >
            {data.has_more && (
              <div className="flex justify-center">
                <button
                  type="button"
                  onClick={() => {
                    stickToBottom.current = false;
                    setLimit((l) => l + PAGE);
                  }}
                  className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  Load earlier messages
                </button>
              </div>
            )}
            {entries.length === 0 && (
              <p className="py-10 text-center text-sm text-slate-400">No messages yet.</p>
            )}
            {entries.map((entry) => {
              const key = dayKey(new Date(entry.at));
              const showDivider = key !== previousDay;
              previousDay = key;
              return (
                <div key={entry.key}>
                  {showDivider && <DayDivider iso={entry.at} />}
                  {entry.kind === "message" ? (
                    <Bubble message={entry.message} />
                  ) : (
                    <CallCard call={entry.call} transcript={entry.transcript} />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
      {data && <ContactPanel thread={data} />}
    </div>
  );
}

// ---------- workspace ----------

export function ChatWorkspace() {
  const router = useRouter();
  const params = useSearchParams();
  const selected = params.get("lead");
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [channel, setChannel] = useState<ChannelFilter>("");
  const [sort, setSort] = useState<ChatSort | "unread">("recent");

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const leads = useChatLeads({
    channel: channel || undefined,
    search: debounced || undefined,
    sort: sort === "unread" ? "recent" : sort,
  });
  const openContact = useOpenContactChat();
  const rows = useMemo(() => {
    const data = leads.data ?? [];
    // "Unread first" is applied on top of the recent-activity order (stable sort).
    return sort === "unread"
      ? [...data].sort((a, b) => Number(b.unread_count > 0) - Number(a.unread_count > 0))
      : data;
  }, [leads.data, sort]);

  const select = (userId: string | null) => {
    router.replace(userId ? `/chat?lead=${userId}` : "/chat", { scroll: false });
  };

  return (
    <div className="-m-4 flex h-[calc(100dvh-4rem)] overflow-hidden bg-white sm:-m-6 lg:-m-8 dark:bg-slate-900">
      {/* Lead list */}
      <div
        className={cn(
          "w-full shrink-0 flex-col border-r border-slate-200 bg-white lg:flex lg:w-80 dark:border-slate-800 dark:bg-slate-900",
          selected ? "hidden" : "flex"
        )}
      >
        <div className="space-y-3 border-b border-slate-200 p-4 dark:border-slate-800">
          <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Chat</h1>
          <label className="relative block">
            <span className="sr-only">Search chats</span>
            <Search
              size={15}
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name or number"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            />
          </label>
          <Select
            value={sort}
            onChange={(v) => setSort(v as ChatSort | "unread")}
            aria-label="Sort chats"
            className="w-full"
          >
            <option value="recent">Latest activity</option>
            <option value="unread">Unread first</option>
            <option value="oldest">Oldest activity</option>
            <option value="name_asc">Name A–Z</option>
            <option value="name_desc">Name Z–A</option>
          </Select>
          <div role="group" aria-label="Filter by channel" className="flex gap-1.5">
            {(
              [
                ["", "All"],
                ["whatsapp", "WhatsApp"],
                ["voice", "Voice"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setChannel(value)}
                aria-pressed={channel === value}
                className={cn(
                  "rounded-full px-3 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500",
                  channel === value
                    ? "bg-primary-600 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {leads.isLoading ? (
            <div className="space-y-3 p-4">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : leads.isError ? (
            <p className="p-4 text-sm text-red-600">Couldn&apos;t load chats.</p>
          ) : rows.length === 0 ? (
            <p className="p-6 text-center text-sm text-slate-400">
              {debounced || channel ? "No chats match." : "No conversations yet."}
            </p>
          ) : (
            rows.map((lead) => (
              <LeadRow
                key={lead.user_id ?? `contact-${lead.contact_id}`}
                lead={lead}
                active={lead.user_id !== null && lead.user_id === selected}
                onSelect={() => {
                  if (lead.user_id) {
                    select(lead.user_id);
                  } else if (lead.contact_id) {
                    openContact.mutate(lead.contact_id, { onSuccess: (r) => select(r.user_id) });
                  }
                }}
              />
            ))
          )}
        </div>
      </div>

      {/* Conversation + contact details */}
      <div className={cn("min-w-0 flex-1 lg:flex", selected ? "flex" : "hidden")}>
        {selected ? (
          <ConversationPane
            userId={selected}
            channel={channel || undefined}
            onBack={() => select(null)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center bg-slate-50 dark:bg-slate-950">
            <EmptyState
              icon={MessagesSquare}
              title="Select a chat"
              description="Pick a lead on the left to see their full WhatsApp and call history in one place."
            />
          </div>
        )}
      </div>
    </div>
  );
}
