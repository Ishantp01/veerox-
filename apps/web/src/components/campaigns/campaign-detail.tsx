"use client";

import { ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Button,
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { formatDateTime, formatPhone } from "@/lib/format";
import { useCampaign } from "@/lib/hooks";
import { CampaignStatusBadge, CampaignTargetStatusBadge } from "./campaign-status-badge";

export interface CampaignDetailProps {
  campaignId: string;
}

export function CampaignDetail({ campaignId }: CampaignDetailProps) {
  const router = useRouter();
  const { data: campaign, isLoading, isError, error, refetch } = useCampaign(campaignId);

  return (
    <div className="mx-auto max-w-7xl">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => router.push("/calling/campaigns")}
        className="mb-4"
      >
        <ArrowLeft size={14} aria-hidden /> Back to campaigns
      </Button>

      <PageHeader
        title={campaign?.name ?? "Campaign"}
        description={campaign ? `Criteria: ${campaign.criteria}` : undefined}
        action={campaign && <CampaignStatusBadge status={campaign.status} />}
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={campaign?.targets.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={5} cols={5} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={<EmptyState title="No contacts in this campaign" description="Nothing was uploaded." />}
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Name</TableHeader>
                <TableHeader>Phone</TableHeader>
                <TableHeader>Status</TableHeader>
                <TableHeader>Qualified</TableHeader>
                <TableHeader>Reason</TableHeader>
                <TableHeader>Called</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {campaign?.targets.map((t) => (
                <TableRow key={t.id}>
                  <TableCell>
                    <span className="font-semibold text-slate-800">{t.name ?? "—"}</span>
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-xs text-slate-600">{formatPhone(t.phone)}</span>
                  </TableCell>
                  <TableCell>
                    <CampaignTargetStatusBadge status={t.status} />
                  </TableCell>
                  <TableCell>
                    {t.qualified === null ? (
                      <span className="text-slate-400">—</span>
                    ) : t.qualified ? (
                      <span className="font-semibold text-emerald-600">Yes</span>
                    ) : (
                      <span className="text-slate-500">No</span>
                    )}
                  </TableCell>
                  <TableCell className="max-w-xs text-xs text-slate-500">
                    {t.disposition_reason ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-slate-500">{formatDateTime(t.called_at)}</TableCell>
                </TableRow>
              ))}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>
    </div>
  );
}
