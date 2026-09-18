import Link from "next/link";
import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { Badge, Input, Table, TableHeader, TableRow, TableCell } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { formatDateTime, formatPhone } from "@/lib/format";
import { useClaimEscalation, useUpdateLead } from "@/lib/hooks";
import type { Escalation } from "@/lib/types";
import { SourceBadge } from "./source-badge";
import { UrgencyBadge } from "./urgency-badge";

/** Inline tags editor for a "lead"-sourced row — a live "queue" entry has no
 * Lead row yet, so there's nothing to attach tags to until it's handled. */
function TagsCell({ escalation }: { escalation: Escalation }) {
  const updateLead = useUpdateLead();
  const { toast } = useToast();
  const [value, setValue] = useState((escalation.tags ?? []).join(", "));
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    setValue((escalation.tags ?? []).join(", "));
  }, [escalation.tags]);

  if (escalation.source !== "lead" || !escalation.id) {
    return <span className="text-sm text-slate-300 dark:text-slate-600">—</span>;
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="flex flex-wrap items-center gap-1 rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        {escalation.tags && escalation.tags.length > 0 ? (
          escalation.tags.map((t) => (
            <Badge key={t} variant="neutral" icon={null}>
              {t}
            </Badge>
          ))
        ) : (
          <span className="text-xs font-medium text-primary-600 hover:underline dark:text-primary-400">
            + Add tags
          </span>
        )}
      </button>
    );
  }

  function handleSave() {
    const tags = value
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    updateLead.mutate(
      { id: escalation.id!, tags: tags.length > 0 ? tags : null },
      {
        onSuccess: () => {
          setEditing(false);
          toast({ title: "Tags updated", variant: "success" });
        },
        onError: (err) =>
          toast({ title: "Update failed", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <div className="flex items-center gap-1">
      <Input
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSave()}
        placeholder="hot, urgent"
        className="h-8 w-32 text-xs"
      />
      <button
        type="button"
        onClick={handleSave}
        disabled={updateLead.isPending}
        aria-label="Save tags"
        className="rounded-sm p-1 text-primary-600 hover:text-primary-800 disabled:opacity-50 dark:text-primary-400"
      >
        <Save size={13} aria-hidden />
      </button>
    </div>
  );
}

export interface EscalationTableProps {
  escalations: Escalation[];
  /** Base path for the conversation link, e.g. "/whatsapp/conversations". */
  conversationBasePath: string;
}

/**
 * Presentational escalation table (UI plan §7.2). Renders the unified
 * Escalation row shape — queue (live) and lead (history) — with source +
 * urgency badges and a link to the conversation when present.
 */
export function EscalationTable({ escalations, conversationBasePath }: EscalationTableProps) {
  const claimEscalation = useClaimEscalation();
  const { toast } = useToast();

  function handleClaim(leadId: string) {
    claimEscalation.mutate(
      { leadId },
      {
        onSuccess: () => toast({ title: "Escalation claimed", variant: "success" }),
        onError: (err) => toast({ title: "Couldn't claim", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <div
      data-tour="page-table"
      className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900"
    >
      <Table>
        <thead>
          <TableRow isHeader>
            <TableHeader>Source</TableHeader>
            <TableHeader>Created</TableHeader>
            <TableHeader>User Phone</TableHeader>
            <TableHeader>Reason</TableHeader>
            <TableHeader>Urgency</TableHeader>
            <TableHeader>Tags</TableHeader>
            <TableHeader>Conversation</TableHeader>
            <TableHeader>Claimed</TableHeader>
          </TableRow>
        </thead>
        <tbody>
          {escalations.map((e, idx) => (
            <TableRow key={e.id ?? `${e.source}_${e.created_at}_${idx}`}>
              <TableCell>
                <SourceBadge source={e.source} />
              </TableCell>
              <TableCell className="text-xs text-slate-500">
                {formatDateTime(e.created_at)}
              </TableCell>
              <TableCell>
                <span className="font-mono text-xs text-slate-600 dark:text-slate-400">
                  {formatPhone(e.user_phone)}
                </span>
              </TableCell>
              <TableCell className="max-w-xs truncate text-slate-700 dark:text-slate-300">
                {e.reason}
              </TableCell>
              <TableCell>
                <UrgencyBadge urgency={e.urgency} />
              </TableCell>
              <TableCell>
                <TagsCell escalation={e} />
              </TableCell>
              <TableCell>
                {e.conversation_id ? (
                  <Link
                    href={`${conversationBasePath}/${e.conversation_id}`}
                    className="rounded-sm text-sm font-semibold text-primary-600 hover:text-primary-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:text-primary-400 dark:hover:text-primary-300"
                  >
                    Open →
                  </Link>
                ) : (
                  <span className="text-sm text-slate-300 dark:text-slate-600">—</span>
                )}
              </TableCell>
              <TableCell>
                {e.claimed_by_name ? (
                  <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
                    {e.claimed_by_name}
                  </span>
                ) : e.id ? (
                  <button
                    type="button"
                    onClick={() => handleClaim(e.id!)}
                    disabled={claimEscalation.isPending}
                    className="rounded-sm text-sm font-semibold text-primary-600 hover:text-primary-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:opacity-50 dark:text-primary-400 dark:hover:text-primary-300"
                  >
                    Claim
                  </button>
                ) : (
                  <span className="text-sm text-slate-300 dark:text-slate-600">—</span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </tbody>
      </Table>
    </div>
  );
}
