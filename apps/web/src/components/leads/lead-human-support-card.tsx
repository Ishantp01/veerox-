"use client";

import Link from "next/link";
import { Headset, MessageSquare } from "lucide-react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle, useToast } from "@/components/ui";
import { useRequestHumanSupport } from "@/lib/hooks";
import type { LeadDetail } from "@/lib/types";

export function conversationHref(channel: string, id: string): string {
  return channel === "voice" ? `/calling/conversations/${id}` : `/whatsapp/conversations/${id}`;
}

/**
 * Human Support for a lead: see whether it's with a human, jump to the
 * conversation, and connect the lead to a human (claims it for you).
 */
export function LeadHumanSupportCard({ lead }: { lead: LeadDetail }) {
  const { toast } = useToast();
  const request = useRequestHumanSupport();
  const isHumanSupport = lead.intent === "human_support";
  const latest = lead.conversations[0];

  function connect() {
    request.mutate(
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
          View the conversation and connect this lead to a human on your team.
        </p>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-3">
          <div className="text-sm text-slate-700 dark:text-slate-300">
            {lead.claimed_by_account_user_id ? (
              <Badge variant="success">With {lead.claimed_by_name ?? "a team member"}</Badge>
            ) : isHumanSupport ? (
              <Badge variant="live">Waiting for a human</Badge>
            ) : (
              <span className="text-slate-500">Not connected to a human.</span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {latest && (
              <Link
                href={conversationHref(latest.channel, latest.id)}
                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-300 px-3.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                <MessageSquare size={14} aria-hidden />
                View conversation
              </Link>
            )}
            {!lead.claimed_by_account_user_id && (
              <Button variant="primary" loading={request.isPending} onClick={connect}>
                {!request.isPending && <Headset size={15} aria-hidden />}
                Connect to human
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
