"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { FileSpreadsheet, Megaphone, PauseCircle, PlayCircle, Upload } from "lucide-react";
import { z } from "zod";

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
  Select,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Textarea,
  useToast,
} from "@/components/ui";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import { downloadCsv } from "@/lib/download-csv";
import { formatDateTime } from "@/lib/format";
import { useCampaigns, useCreateCampaign, usePauseCampaign, useResumeCampaign } from "@/lib/hooks";
import { CampaignStatusBadge } from "./campaign-status-badge";

async function downloadSampleContactFile(format: "csv" | "xlsx"): Promise<void> {
  await downloadCsv(`/admin/campaigns/sample.${format}`, `campaign-contacts-sample.${format}`);
}

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024;

const campaignSchema = z.object({
  name: z.string().trim().min(1, "Campaign name is required"),
  criteria: z
    .string()
    .trim()
    .min(1, "Qualification criteria is required")
    .max(5000, "Qualification criteria must be under 5000 characters"),
});

type CampaignFieldErrors = Partial<Record<"name" | "criteria" | "file", string>>;

function validateContactFile(file: File | null): string | null {
  if (!file) return "A contact list file is required";
  const lowerName = file.name.toLowerCase();
  if (!lowerName.endsWith(".csv") && !lowerName.endsWith(".xlsx")) {
    return "File must be a .csv or .xlsx";
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return "File must be 10MB or smaller";
  }
  return null;
}

/**
 * Bulk-upload a lead list, criteria included, and let the background worker
 * — the voice dialer (apps/api/workers/campaign_dialer.py) or the WhatsApp
 * dispatcher (apps/api/workers/whatsapp_dispatcher.py), per campaign channel
 * — reach each one. The AI's qualify_lead tool call is what decides whether
 * a contact reaches the CRM, on either channel.
 */
export function CampaignsView() {
  const router = useRouter();
  const { toast } = useToast();
  const [channelFilter, setChannelFilter] = useState<"voice" | "whatsapp" | "">("");
  const { data, isLoading, isError, error, refetch } = useCampaigns(channelFilter || undefined);
  const campaigns = data ?? [];

  const [name, setName] = useState("");
  const [criteria, setCriteria] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [channel, setChannel] = useState<"voice" | "whatsapp">("voice");
  const [fieldErrors, setFieldErrors] = useState<CampaignFieldErrors>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  const createCampaign = useCreateCampaign();
  const pauseCampaign = usePauseCampaign();
  const resumeCampaign = useResumeCampaign();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const errors: CampaignFieldErrors = {};
    const parsed = campaignSchema.safeParse({ name, criteria });
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof CampaignFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
    }
    const fileError = validateContactFile(file);
    if (fileError) errors.file = fileError;

    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    if (!file) return;
    setFieldErrors({});

    createCampaign.mutate(
      { name, criteria, file, channel },
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
          setChannel("voice");
          setFieldErrors({});
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
        title="Campaigns"
        description="Upload a lead list with qualification criteria — the AI agent reaches each one by voice or WhatsApp, and only qualified prospects reach the CRM."
        action={
          <Select
            value={channelFilter}
            onChange={(v) => setChannelFilter(v as "voice" | "whatsapp" | "")}
            aria-label="Filter by channel"
          >
            <option value="">All channels</option>
            <option value="voice">Voice</option>
            <option value="whatsapp">WhatsApp</option>
          </Select>
        }
      />

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>New campaign</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
            <div className="grid gap-4 sm:grid-cols-3">
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
                  aria-invalid={fieldErrors.name ? true : undefined}
                  aria-describedby={fieldErrors.name ? "campaign-name-error" : undefined}
                />
                {fieldErrors.name && (
                  <p id="campaign-name-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.name}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="campaign-channel" required>
                  Channel
                </Label>
                <Select
                  id="campaign-channel"
                  value={channel}
                  onChange={(v) => setChannel(v as "voice" | "whatsapp")}
                  className="w-full"
                >
                  <option value="voice">Voice (calling)</option>
                  <option value="whatsapp">WhatsApp</option>
                </Select>
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
                  aria-invalid={fieldErrors.file ? true : undefined}
                  aria-describedby={fieldErrors.file ? "campaign-file-error" : undefined}
                  className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-800 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-slate-700 hover:file:bg-slate-200 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:file:bg-slate-800 dark:file:text-slate-200"
                />
                {fieldErrors.file && (
                  <p id="campaign-file-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.file}
                  </p>
                )}
                <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                  Needs a &quot;phone&quot; column (E.164, e.g. +919876543210) and an optional
                  &quot;name&quot; column — same file works for either channel above.
                </p>
                <div className="mt-2 flex gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => downloadSampleContactFile("csv")}
                    title="Download a sample CSV showing the expected columns"
                  >
                    <FileSpreadsheet size={13} aria-hidden />
                    Sample CSV
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => downloadSampleContactFile("xlsx")}
                    title="Download a sample Excel file showing the expected columns"
                  >
                    <FileSpreadsheet size={13} aria-hidden />
                    Sample XLSX
                  </Button>
                </div>
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
                aria-invalid={fieldErrors.criteria ? true : undefined}
                aria-describedby={fieldErrors.criteria ? "campaign-criteria-error" : undefined}
              />
              {fieldErrors.criteria && (
                <p id="campaign-criteria-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.criteria}
                </p>
              )}
              <p className="mt-1.5 text-xs text-slate-400">
                The AI agent asks questions to judge each prospect against this bar, then records its
                verdict — only prospects it marks interested become CRM leads.
              </p>
            </div>
            <div>
              <Button type="submit" variant="primary" loading={createCampaign.isPending}>
                {!createCampaign.isPending && <Upload size={15} aria-hidden />}
                {channel === "voice" ? "Start Calling" : "Start Sending"}
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
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
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
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Name</TableHeader>
                <TableHeader>Channel</TableHeader>
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
                    className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
                    onClick={() => router.push(`/automation/campaigns/${c.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        router.push(`/automation/campaigns/${c.id}`);
                      }
                    }}
                  >
                    <TableCell>
                      <span className="font-semibold text-slate-800 dark:text-slate-100">{c.name}</span>
                    </TableCell>
                    <TableCell>
                      <ChannelBadge channel={c.channel} />
                    </TableCell>
                    <TableCell>
                      <CampaignStatusBadge status={c.status} />
                    </TableCell>
                    <TableCell className="text-xs text-slate-600 dark:text-slate-400">
                      {done} / {total} called
                      {c.counts.calling > 0 && (
                        <span className="ml-1.5 text-primary-500">({c.counts.calling} in progress)</span>
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
