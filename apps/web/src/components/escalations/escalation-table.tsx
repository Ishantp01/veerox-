import Link from "next/link";
import { Table, TableHeader, TableRow, TableCell } from "@/components/ui";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { Escalation } from "@/lib/types";
import { SourceBadge } from "./source-badge";
import { UrgencyBadge } from "./urgency-badge";

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
  return (
    <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
      <Table>
        <thead>
          <TableRow isHeader>
            <TableHeader>Source</TableHeader>
            <TableHeader>Created</TableHeader>
            <TableHeader>User Phone</TableHeader>
            <TableHeader>Reason</TableHeader>
            <TableHeader>Urgency</TableHeader>
            <TableHeader>Conversation</TableHeader>
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
                <span className="font-mono text-xs text-slate-600">
                  {formatPhone(e.user_phone)}
                </span>
              </TableCell>
              <TableCell className="max-w-xs truncate text-slate-700">
                {e.reason}
              </TableCell>
              <TableCell>
                <UrgencyBadge urgency={e.urgency} />
              </TableCell>
              <TableCell>
                {e.conversation_id ? (
                  <Link
                    href={`${conversationBasePath}/${e.conversation_id}`}
                    className="rounded-sm text-sm font-semibold text-indigo-600 hover:text-indigo-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
                  >
                    Open →
                  </Link>
                ) : (
                  <span className="text-sm text-slate-300">—</span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </tbody>
      </Table>
    </div>
  );
}
