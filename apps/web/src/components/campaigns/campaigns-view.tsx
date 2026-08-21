"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Calendar, FileSpreadsheet, Megaphone, PauseCircle, PlayCircle, Upload } from "lucide-react";
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
import { CampaignChannelBadge } from "@/components/conversations/channel-badge";
import {
  TemplateParamMapper,
  guessTemplateParamSource,
  resolveTemplateParams,
  type TemplateParamSource,
} from "@/components/whatsapp/template-param-mapper";
import { downloadCsv } from "@/lib/download-csv";
import { formatDateTime } from "@/lib/format";
import {
  useCampaigns,
  useCreateCampaign,
  usePauseCampaign,
  useResumeCampaign,
  useScheduleCampaign,
  useTemplates,
  type CampaignStartMode,
} from "@/lib/hooks";
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

type CampaignFieldErrors = Partial<
  Record<"name" | "criteria" | "file" | "scheduledAt" | "templateParams", string>
>;

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

const START_MODE_BUTTON_LABEL: Record<CampaignStartMode, string> = {
  draft: "Save as Draft",
  now: "Start Campaign",
  scheduled: "Schedule Campaign",
};

/**
 * Bulk-upload a lead list, criteria included, and let the background worker
 * — the voice dialer (apps/api/workers/campaign_dialer.py) or the WhatsApp
 * dispatcher (apps/api/workers/whatsapp_dispatcher.py) — reach each one.
 * Each row's own "call"/"whatsapp" columns decide its channel(s), so one
 * upload can mix call-only, WhatsApp-only, and both-channel contacts — see
 * apps/api/routers/admin.py's _create_campaigns_from_rows. The AI's
 * qualify_lead tool call is what decides whether a contact reaches the CRM.
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
  const [startMode, setStartMode] = useState<CampaignStartMode>("draft");
  const [scheduledAt, setScheduledAt] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [paramSources, setParamSources] = useState<TemplateParamSource[]>([]);
  const [paramCustomValues, setParamCustomValues] = useState<string[]>([]);
  const [customMessage, setCustomMessage] = useState("");
  const [fieldErrors, setFieldErrors] = useState<CampaignFieldErrors>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [reschedulingId, setReschedulingId] = useState<string | null>(null);
  const [rescheduleValue, setRescheduleValue] = useState("");

  const { data: templatesData } = useTemplates({ active: true });
  // Every Meta-approved template — including ones with {{1}}/{{2}} params,
  // which are mapped below (contact name from the file, the send date/time,
  // or a fixed value) rather than hidden.
  const templates = (templatesData ?? []).filter((t) => t.meta_status === "APPROVED");
  const selectedTemplate = templates.find((t) => t.id === templateId);

  function handleTemplateChange(id: string) {
    setTemplateId(id);
    const template = templates.find((t) => t.id === id);
    if (!template) {
      setParamSources([]);
      setParamCustomValues([]);
      return;
    }
    setParamSources(template.param_labels.map(guessTemplateParamSource));
    setParamCustomValues(template.param_labels.map(() => ""));
  }

  const createCampaign = useCreateCampaign();
  const pauseCampaign = usePauseCampaign();
  const resumeCampaign = useResumeCampaign();
  const scheduleCampaign = useScheduleCampaign();

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
    if (startMode === "scheduled" && !scheduledAt) {
      errors.scheduledAt = "Pick a date and time to schedule this campaign";
    }
    const hasEmptyCustomParam =
      selectedTemplate?.param_labels.some(
        (_, i) => (paramSources[i] ?? "custom") === "custom" && !(paramCustomValues[i] ?? "").trim()
      ) ?? false;
    if (hasEmptyCustomParam) {
      errors.templateParams = "Fill in every custom placeholder value, or pick a different source";
    }

    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    if (!file) return;
    setFieldErrors({});

    const templateParams = selectedTemplate
      ? resolveTemplateParams(selectedTemplate.param_labels, paramSources, paramCustomValues)
      : undefined;

    createCampaign.mutate(
      {
        name,
        criteria,
        file,
        startMode,
        scheduledStartAt: startMode === "scheduled" ? new Date(scheduledAt).toISOString() : undefined,
        templateName: selectedTemplate?.name,
        templateLanguage: selectedTemplate?.language,
        templateParams,
        customMessage: customMessage.trim() || undefined,
      },
      {
        onSuccess: (result) => {
          const modeText =
            startMode === "now"
              ? "Started"
              : startMode === "scheduled"
                ? "Scheduled"
                : "Saved as draft";
          toast({
            title: `${modeText} — ${result.imported} contact(s) staged`,
            description:
              result.skipped > 0
                ? `Skipped ${result.skipped} row(s) — see errors below.`
                : undefined,
            variant: result.skipped > 0 ? "info" : "success",
          });
          setName("");
          setCriteria("");
          setFile(null);
          setStartMode("draft");
          setScheduledAt("");
          setTemplateId("");
          setParamSources([]);
          setParamCustomValues([]);
          setCustomMessage("");
          setFieldErrors({});
          if (fileInputRef.current) fileInputRef.current.value = "";
        },
        onError: (err) => {
          toast({
            title: "Could not create campaign",
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

  function handleReschedule(id: string) {
    if (!rescheduleValue) return;
    scheduleCampaign.mutate(
      { id, scheduledStartAt: new Date(rescheduleValue).toISOString() },
      {
        onSuccess: () => {
          setReschedulingId(null);
          setRescheduleValue("");
        },
        onError: (err) =>
          toast({ title: "Could not reschedule campaign", description: err.message, variant: "error" }),
      }
    );
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
                <Label htmlFor="campaign-start-mode" required>
                  When to start
                </Label>
                <Select
                  id="campaign-start-mode"
                  value={startMode}
                  onChange={(v) => setStartMode(v as CampaignStartMode)}
                  className="w-full"
                >
                  <option value="draft">Save as draft (start later)</option>
                  <option value="now">Start now</option>
                  <option value="scheduled">Schedule for…</option>
                </Select>
                {startMode === "scheduled" && (
                  <>
                    <input
                      type="datetime-local"
                      value={scheduledAt}
                      onChange={(e) => setScheduledAt(e.target.value)}
                      aria-invalid={fieldErrors.scheduledAt ? true : undefined}
                      aria-describedby={fieldErrors.scheduledAt ? "campaign-scheduled-at-error" : undefined}
                      className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                    />
                    {fieldErrors.scheduledAt && (
                      <p id="campaign-scheduled-at-error" className="mt-1.5 text-xs text-red-600">
                        {fieldErrors.scheduledAt}
                      </p>
                    )}
                  </>
                )}
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
                  Needs a &quot;phone&quot; column (E.164, e.g. +919876543210), an optional
                  &quot;name&quot; column, and &quot;call&quot;/&quot;whatsapp&quot; columns (yes/no)
                  to pick each contact&apos;s channel(s) — a row can be call-only, WhatsApp-only, or
                  both.
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
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="campaign-template">WhatsApp template (optional)</Label>
                <Select
                  id="campaign-template"
                  value={templateId}
                  onChange={handleTemplateChange}
                  className="w-full"
                >
                  <option value="">No template — send free text (may fail outside 24h reply window)</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} ({t.language})
                      {t.param_labels.length > 0 ? ` — ${t.param_labels.length} param(s)` : ""}
                    </option>
                  ))}
                </Select>
                <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                  {templates.length === 0
                    ? "No Meta-approved templates yet — create and approve one on the WhatsApp Templates page."
                    : "Used for every WhatsApp contact in this upload — works even for contacts who haven't messaged you recently."}
                </p>
              </div>
              <div>
                <Label htmlFor="campaign-custom-message">Custom message (optional)</Label>
                <Textarea
                  id="campaign-custom-message"
                  rows={2}
                  value={customMessage}
                  onChange={(e) => setCustomMessage(e.target.value)}
                  placeholder="Sent right after the template — leave blank to send only the template."
                />
              </div>
            </div>
            {selectedTemplate && selectedTemplate.param_labels.length > 0 && (
              <div>
                <TemplateParamMapper
                  paramLabels={selectedTemplate.param_labels}
                  sources={paramSources}
                  customValues={paramCustomValues}
                  onSourceChange={(i, source) =>
                    setParamSources((prev) => prev.map((s, idx) => (idx === i ? source : s)))
                  }
                  onCustomValueChange={(i, value) =>
                    setParamCustomValues((prev) => prev.map((v, idx) => (idx === i ? value : v)))
                  }
                  nameSourceLabel="Contact name (from file)"
                />
                {fieldErrors.templateParams && (
                  <p className="mt-1.5 text-xs text-red-600">{fieldErrors.templateParams}</p>
                )}
              </div>
            )}
            <div>
              <Button type="submit" variant="primary" loading={createCampaign.isPending}>
                {!createCampaign.isPending && <Upload size={15} aria-hidden />}
                {START_MODE_BUTTON_LABEL[startMode]}
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
                <SkeletonRows rows={3} cols={6} />
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
                const isInert = c.status === "draft" || c.status === "scheduled";
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
                      <CampaignChannelBadge channel={c.channel} />
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-0.5">
                        <CampaignStatusBadge status={c.status} />
                        {c.status === "scheduled" && c.scheduled_start_at && (
                          <span className="text-[11px] text-slate-400 dark:text-slate-500">
                            {formatDateTime(c.scheduled_start_at)}
                          </span>
                        )}
                      </div>
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
                      {isInert ? (
                        reschedulingId === c.id ? (
                          <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                            <input
                              type="datetime-local"
                              value={rescheduleValue}
                              onChange={(e) => setRescheduleValue(e.target.value)}
                              className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                            />
                            <Button
                              variant="primary"
                              size="sm"
                              loading={scheduleCampaign.isPending}
                              onClick={() => handleReschedule(c.id)}
                            >
                              Save
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => setReschedulingId(null)}>
                              Cancel
                            </Button>
                          </div>
                        ) : (
                          <div className="flex gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                handlePauseResume(c.id, false);
                              }}
                            >
                              <PlayCircle size={13} aria-hidden /> Start now
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                setReschedulingId(c.id);
                                setRescheduleValue("");
                              }}
                            >
                              <Calendar size={13} aria-hidden />
                              {c.status === "scheduled" ? "Reschedule" : "Schedule"}
                            </Button>
                          </div>
                        )
                      ) : (
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
