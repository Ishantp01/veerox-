"use client";

import Link from "next/link";
import { Headset, MessageSquare } from "lucide-react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Skeleton,
  useToast,
} from "@/components/ui";
import { useLeadHumanSupport, useRequestHumanSupport } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import type { Lead } from "@/lib/types";

export function conversationHref(channel: string, id: string): string {
  return channel === "voice" ? `/calling/conversations/${id}` : `/whatsapp/conversations/${id}`;
}

function metaString(lead: Lead, key: string): string | null {
  const v = lead.metadata_?.[key];
  return typeof v === "string" && v ? v : null;
}

/**
 * All Human Support requests for this lead's contact (newest first), each with
 * its reason, who has it, and a link to its conversation — plus a button to
 * connect the lead to a human (claims it for you).
 */
export function LeadHumanSupportCard({ lead }: { lead: Lead }) {
  const { toast } = useToast();
  const requests = useLeadHumanSupport(lead.id);
  const connect = useRequestHumanSupport();
  const rows = requests.data ?? [];
  const isMine = rows.some((r) => r.id === lead.id);
  const canConnect = !lead.claimed_by_account_user_id;

  function handleConnect() {
    connect.mutate(
      { leadId: lead.id },
      {
        onSuccess: () => toast({ title: "Connected to Human Support", variant: "success" }),
        onError: (err) =>
          toast({ title: "Couldn't connect", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Card id="human-support">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Headset size={15} aria-hidden className="text-slate-400" />
          <CardTitle>Human Support</CardTitle>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Every time this lead needed a human, with the conversation for each.
        </p>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-4">
          {requests.isLoading ? (
            <Skeleton className="h-16 w-full rounded-xl" />
          ) : rows.length === 0 ? (
            <p className="text-sm text-slate-500">No Human Support requests for this lead yet.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-slate-100 rounded-xl border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
              {rows.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-3 px-3.5 py-3 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium text-slate-800 dark:text-slate-100">
                      {metaString(r, "reason") ?? "Requested a human"}
                    </p>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {formatDateTime(r.created_at)}
                      {metaString(r, "urgency") ? ` · ${metaString(r, "urgency")} urgency` : ""}
                    </p>
                  </div>
                  {r.claimed_by_account_user_id ? (
                    <Badge variant="success">With {r.claimed_by_name ?? "team"}</Badge>
                  ) : (
                    <Badge variant="live">Waiting</Badge>
                  )}
                  {r.conversation_id && r.channel && (
                    <Link
                      href={conversationHref(r.channel, r.conversation_id)}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
                    >
                      <MessageSquare size={13} aria-hidden />
                      Conversation
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
          {canConnect && (
            <Button
              variant="primary"
              className="self-start"
              loading={connect.isPending}
              onClick={handleConnect}
            >
              {!connect.isPending && <Headset size={15} aria-hidden />}
              {isMine ? "Take this request" : "Connect to human"}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
