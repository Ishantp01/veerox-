"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, CheckCircle2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { HumanSupportTable } from "@/components/human-support/human-support-table";
import { leadToHumanSupport, queueEntryToHumanSupport } from "@/components/human-support/human-support-view";
import { EmptyState, SkeletonRows, Table } from "@/components/ui";
import { formatPhone } from "@/lib/format";
import { useHumanSupport, useLead, useLeadHumanSupport } from "@/lib/hooks";

const digits = (v: string | null | undefined) => (v ?? "").replace(/\D/g, "");

/** Human Support requests of one lead only — opened from the Leads table's
 * Human Support "View" button. */
export default function LeadHumanSupportPage() {
  const params = useParams();
  const id = typeof params.id === "string" ? params.id : (params.id?.[0] ?? "");
  const lead = useLead(id);
  const requests = useLeadHumanSupport(id);
  // Live queue entries (pending pickup, straight from Redis) have no lead row
  // yet, so match them to this lead by phone.
  const live = useHumanSupport();
  const leadDigits = digits(lead.data?.phone);
  const queueRows = leadDigits
    ? (live.data?.queue ?? [])
        .map(queueEntryToHumanSupport)
        .filter((r) => digits(r.user_phone) === leadDigits)
    : [];
  const rows = [...queueRows, ...(requests.data ?? []).map(leadToHumanSupport)];

  return (
    <div className="mx-auto max-w-7xl">
      <Link
        href="/crm/leads"
        className="mb-4 inline-flex items-center gap-1.5 rounded-md text-sm text-slate-500 transition-colors hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        <ArrowLeft size={15} aria-hidden />
        Leads
      </Link>
      <PageHeader
        title="Human Support"
        description={
          lead.data
            ? `Human Support requests for ${lead.data.name ?? formatPhone(lead.data.phone)}`
            : "Human Support requests for this lead"
        }
      />
      <QueryBoundary
        isLoading={requests.isLoading || lead.isLoading}
        isError={requests.isError}
        error={requests.error}
        isEmpty={rows.length === 0}
        onRetry={() => requests.refetch()}
        loadingFallback={
          <Table>
            <tbody>
              <SkeletonRows rows={3} cols={5} />
            </tbody>
          </Table>
        }
        emptyFallback={
          <EmptyState
            icon={CheckCircle2}
            title="No Human Support requests"
            description="This lead has not needed a human yet."
          />
        }
      >
        <HumanSupportTable humanSupport={rows} conversationBasePath="/conversations" />
      </QueryBoundary>
    </div>
  );
}
