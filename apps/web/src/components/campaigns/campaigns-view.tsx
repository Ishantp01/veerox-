"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Megaphone, PauseCircle, PlayCircle, Upload } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Input,
  Label,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Textarea,
  useToast,
} from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { useCampaigns, useCreateCampaign, usePauseCampaign, useResumeCampaign } from "@/lib/hooks";
import { CampaignStatusBadge } from "./campaign-status-badge";

/**
 * Bulk-upload a lead list, criteria included, and let the background dialer
 * (apps/api/workers/campaign_dialer.py) call each one — the AI's
 * qualify_lead tool call is what decides whether a contact reaches the CRM.
 */
export function CampaignsView() {
  const router = useRouter();
  const { toast } = useToast();
  const { data, isLoading, isError, error, refetch } = useCampaigns();
  const campaigns = data ?? [];

  const [name, setName] = useState("");
  const [criteria, setCriteria] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const createCampaign = useCreateCampaign();
  const pauseCampaign = usePauseCampaign();
  const resumeCampaign = useResumeCampaign();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;

    createCampaign.mutate(
      { name, criteria, file },
      {
        onSuccess: (result) => {
          toast({
            title: "Campaign started",
            description:
              result.skipped > 0
                ? `Staged ${result.imported} contact(s) to call, skipped ${result.skipped} row(s).`
                : `Staged ${result.imported} contact(s) to call.`,
            variant: result.skipped > 0 ? "info" : "success",
          });
          setName("");
          setCriteria("");
          setFile(null);
          if (fileInputRef.current) fileInputRef.current.value = "";
        },
        onError: (err) => {
          toast({
            title: "Could not start campaign",
            description: err.message,
            variant: "error",
          });
        },
      }
    );
  }

  function handlePauseResume(id: string, isRunning: boolean) {
    const mutation = isRunning ? pauseCampaign : resumeCampaign;
    mutation.mutate(id, {
      onError: (err) =>
        toast({ title: "Could not update campaign", description: err.message, variant: "error" }),
    });
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Calling Campaigns"
        description="Upload a lead list with qualification criteria — the AI agent calls each one and only qualified prospects reach the CRM."
      />

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>New campaign</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="campaign-name" required>
                  Campaign name
                </Label>
                <Input
                  id="campaign-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="July outreach"
                  required
                />
              </div>
              <div>
                <Label htmlFor="campaign-file" required>
                  Contact list (.csv or .xlsx)
                </Label>
                <input
                  ref={fileInputRef}
                  id="campaign-file"
                  type="file"
                  accept=".csv,.xlsx"
                  required
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-800 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-slate-700 hover:file:bg-slate-200"
                />
                <p className="mt-1.5 text-xs text-slate-400">Must include a &quot;phone&quot; column.</p>
              </div>
            </div>
            <div>
              <Label htmlFor="campaign-criteria" required>
                Qualification criteria
              </Label>
              <Textarea
                id="campaign-criteria"
                value={criteria}
                onChange={(e) => setCriteria(e.target.value)}
                placeholder="e.g. Prospect must confirm interest in a demo and have a budget above $5,000."
                required
              />
              <p className="mt-1.5 text-xs text-slate-400">
                The AI agent asks questions to judge each prospect against this bar, then records its
                verdict — only prospects it marks interested become CRM leads.
              </p>
            </div>
            <div>
              <Button type="submit" variant="primary" loading={createCampaign.isPending}>
                {!createCampaign.isPending && <Upload size={15} aria-hidden />}
                Start Calling
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={campaigns.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            <Table>
              <tbody>
                <SkeletonRows rows={3} cols={5} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState
            icon={Megaphone}
            title="No campaigns yet"
            description="Upload a contact list above to start auto-dialing and qualifying leads."
          />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Name</TableHeader>
                <TableHeader>Status</TableHeader>
                <TableHeader>Progress</TableHeader>
                <TableHeader>Qualified</TableHeader>
                <TableHeader>Created</TableHeader>
                <TableHeader>Actions</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {campaigns.map((c) => {
                const total = c.counts.pending + c.counts.calling + c.counts.completed + c.counts.failed;
                const done = c.counts.completed + c.counts.failed;
                return (
                  <TableRow
                    key={c.id}
                    role="link"
                    tabIndex={0}
                    className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500"
                    onClick={() => router.push(`/calling/campaigns/${c.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        router.push(`/calling/campaigns/${c.id}`);
                      }
                    }}
                  >
                    <TableCell>
                      <span className="font-semibold text-slate-800">{c.name}</span>
                    </TableCell>
                    <TableCell>
                      <CampaignStatusBadge status={c.status} />
                    </TableCell>
                    <TableCell className="text-xs text-slate-600">
                      {done} / {total} called
                      {c.counts.calling > 0 && (
                        <span className="ml-1.5 text-indigo-500">({c.counts.calling} in progress)</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <span className="font-semibold text-emerald-600">{c.counts.qualified}</span>
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">{formatDateTime(c.created_at)}</TableCell>
                    <TableCell>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          handlePauseResume(c.id, c.status === "running");
                        }}
                        disabled={c.status === "completed"}
                      >
                        {c.status === "running" ? (
                          <>
                            <PauseCircle size={13} aria-hidden /> Pause
                          </>
                        ) : (
                          <>
                            <PlayCircle size={13} aria-hidden /> Resume
                          </>
                        )}
                      </Button>
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
