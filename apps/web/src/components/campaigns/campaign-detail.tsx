"use client";

import { ArrowLeft, RotateCcw, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  EmptyState,
  Input,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useToast,
} from "@/components/ui";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import { formatDateTime, formatPhone } from "@/lib/format";
import { findConversationByPhone, useCampaign, useRetryCampaignTarget } from "@/lib/hooks";
import { CampaignStatusBadge, CampaignTargetStatusBadge } from "./campaign-status-badge";

export interface CampaignDetailProps {
  campaignId: string;
}

export function CampaignDetail({ campaignId }: CampaignDetailProps) {
  const router = useRouter();
  const { toast } = useToast();
  const { data: campaign, isLoading, isError, error, refetch } = useCampaign(campaignId);
  const retryTarget = useRetryCampaignTarget();
  const [resolvingTargetId, setResolvingTargetId] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const allTargets = campaign?.targets ?? [];
  const term = search.trim().toLowerCase();
  const targets = term
    ? allTargets.filter((t) =>
        [t.name, t.phone, t.disposition_reason, ...(t.tags ?? [])]
          .filter(Boolean)
          .some((f) => f!.toLowerCase().includes(term)),
      )
    : allTargets;

  async function openConversation(targetId: string, conversationId: string | null, phone: string) {
    if (conversationId) {
      router.push(`/conversations/${conversationId}`);
      return;
    }
    setResolvingTargetId(targetId);
    try {
      const conversation = await findConversationByPhone(phone);
      if (conversation) {
        router.push(`/conversations/${conversation.id}`);
      } else {
        toast({
          title: "No conversation yet",
          description: "This contact hasn't replied, so there's nothing to show yet.",
        });
      }
    } catch (err) {
      toast({
        title: "Couldn't open conversation",
        description: err instanceof Error ? err.message : undefined,
        variant: "error",
      });
    } finally {
      setResolvingTargetId(null);
    }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => router.push("/automation/campaigns")}
        className="mb-4"
      >
        <ArrowLeft size={14} aria-hidden /> Back to campaigns
      </Button>

      <PageHeader
        title={campaign?.name ?? "Campaign"}
        description={campaign ? `Criteria: ${campaign.criteria}` : undefined}
        action={
          campaign && (
            <div className="flex flex-wrap items-center gap-3">
              <div className="relative">
                <Search
                  size={14}
                  aria-hidden
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                />
                <Input
                  id="campaign-target-search"
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search name, number, or tag…"
                  aria-label="Search contacts by name, number, or tag"
                  className="w-56 pl-8"
                />
              </div>
              {campaign.status === "scheduled" && campaign.scheduled_start_at && (
                <span className="text-xs text-slate-400 dark:text-slate-500">
                  Starts {formatDateTime(campaign.scheduled_start_at)}
                </span>
              )}
              <CampaignStatusBadge status={campaign.status} />
            </div>
          )
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={targets.length === 0 && !search}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={5} cols={9} />
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
                <TableHeader>Channel</TableHeader>
                <TableHeader>Status</TableHeader>
                <TableHeader>Qualified</TableHeader>
                <TableHeader>Reason</TableHeader>
                <TableHeader>Tags</TableHeader>
                <TableHeader>Called</TableHeader>
                <TableHeader />
              </TableRow>
            </thead>
            <tbody>
              {targets.map((t) => {
                const isCompleted = t.status === "completed";
                const isResolving = resolvingTargetId === t.id;
                return (
                  <TableRow
                    key={t.id}
                    onClick={isCompleted ? () => openConversation(t.id, t.conversation_id, t.phone) : undefined}
                    className={isCompleted ? `cursor-pointer${isResolving ? " opacity-60" : ""}` : undefined}
                  >
                    <TableCell>
                      <span className="font-semibold text-slate-800 dark:text-slate-100">{t.name ?? "—"}</span>
                    </TableCell>
                    <TableCell>
                      <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{formatPhone(t.phone)}</span>
                    </TableCell>
                    <TableCell>
                      <ChannelBadge channel={t.channel} />
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
                    <TableCell>
                      {t.tags && t.tags.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {t.tags.map((tag) => (
                            <Badge key={tag} variant="neutral" icon={null}>
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">{formatDateTime(t.called_at)}</TableCell>
                    <TableCell>
                      {t.status === "failed" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={retryTarget.isPending && retryTarget.variables?.targetId === t.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            retryTarget.mutate(
                              { campaignId, targetId: t.id },
                              {
                                onSuccess: () =>
                                  toast({ title: "Retry queued", variant: "success" }),
                                onError: (err) =>
                                  toast({
                                    title: "Couldn't retry",
                                    description: err.message,
                                    variant: "error",
                                  }),
                              },
                            );
                          }}
                        >
                          <RotateCcw size={13} aria-hidden /> Retry
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>
    </div>
  );
}
