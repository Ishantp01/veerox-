"use client";

import { useRouter } from "next/navigation";
import { Badge, Button, Table, TableHeader, TableRow, TableCell, useToast } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { Lead, LeadDetail } from "@/lib/types";
import { IntentBadge } from "./intent-badge";
import { StatusBadge } from "./status-badge";

export interface LeadTableProps {
  leads: Lead[];
  /** Base path for row navigation, e.g. "/whatsapp/leads". Rows are inert without it. */
  detailBasePath?: string;
}

/**
 * Presentational lead table (UI plan §7.2) — aware of the Lead type but not of
 * fetching. Columns: Name, Phone, Intent, Tags, Status, Conversation, Human Support, Created. Rows navigate to
 * `${detailBasePath}/${lead.id}` when provided (dashboard/CRM detail view).
 */
export function LeadTable({ leads, detailBasePath }: LeadTableProps) {
  const router = useRouter();
  const { toast } = useToast();

  /** Open the lead's conversation (not the lead page). A Human Support lead
   * carries the conversation that raised it; otherwise use the newest
   * conversation that actually has messages (calls that dropped before anyone
   * spoke are empty), falling back to the newest of all. */
  async function openConversation(lead: Lead) {
    // The unified CRM route (highlights "Conversations" in the sidebar), not
    // the per-channel /calling or /whatsapp one, which would light up AI Calling.
    const go = (id: string) => router.push(`/conversations/${id}`);
    if (lead.conversation_id) {
      go(lead.conversation_id);
      return;
    }
    try {
      const detail = await apiFetch<LeadDetail>(`/admin/leads/${lead.id}`);
      const conv =
        detail.conversations.find((c) => (c.message_count ?? 0) > 0) ?? detail.conversations[0];
      if (!conv) {
        toast({ title: "No conversation yet", description: "This lead has no conversation.", variant: "error" });
        return;
      }
      go(conv.id);
    } catch (err) {
      toast({
        title: "Couldn't open conversation",
        description: err instanceof Error ? err.message : undefined,
        variant: "error",
      });
    }
  }

  return (
    <div
      data-tour="page-table"
      className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900"
    >
      <Table>
        <thead>
          <TableRow isHeader>
            <TableHeader>Name</TableHeader>
            <TableHeader>Phone</TableHeader>
            <TableHeader>Intent</TableHeader>
            <TableHeader>Tags</TableHeader>
            <TableHeader title="Where this lead sits in your sales pipeline: New → Contacted → Qualified → Converted/Lost.">
              Status
            </TableHeader>
            <TableHeader>Conversation</TableHeader>
            <TableHeader>Human Support</TableHeader>
            <TableHeader>Created</TableHeader>
          </TableRow>
        </thead>
        <tbody>
          {leads.map((lead) => {
            const href = detailBasePath ? `${detailBasePath}/${lead.id}` : undefined;
            return (
              <TableRow
                key={lead.id}
                role={href ? "link" : undefined}
                tabIndex={href ? 0 : undefined}
                aria-label={href ? `Open lead ${lead.name ?? lead.phone ?? lead.id}` : undefined}
                onClick={href ? () => router.push(href) : undefined}
                onKeyDown={
                  href
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          router.push(href);
                        }
                      }
                    : undefined
                }
                className={
                  href
                    ? "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
                    : undefined
                }
              >
                <TableCell>
                  <span className="font-semibold text-slate-800 dark:text-slate-100">{lead.name ?? "—"}</span>
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs text-slate-600 dark:text-slate-400">
                    {formatPhone(lead.phone)}
                  </span>
                </TableCell>
                <TableCell>
                  <IntentBadge intent={lead.intent} />
                </TableCell>
                <TableCell>
                  {lead.tags && lead.tags.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {lead.tags.map((tag) => (
                        <Badge key={tag} variant="neutral" icon={null}>
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <span className="text-slate-400 dark:text-slate-600">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <StatusBadge status={lead.status} />
                </TableCell>
                <TableCell>
                  {href ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        void openConversation(lead);
                      }}
                    >
                      View
                    </Button>
                  ) : (
                    <span className="text-slate-400 dark:text-slate-600">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    {lead.intent === "human_support" && !lead.claimed_by_account_user_id && (
                      <Badge variant="live">Waiting</Badge>
                    )}
                    {href ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          router.push(`/human-support/lead/${lead.id}`);
                        }}
                      >
                        View
                      </Button>
                    ) : (
                      lead.intent !== "human_support" && (
                        <span className="text-slate-400 dark:text-slate-600">—</span>
                      )
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-xs text-slate-500">
                  {formatDateTime(lead.created_at)}
                </TableCell>
              </TableRow>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
