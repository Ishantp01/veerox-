"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Download, FileSpreadsheet, Search, Upload, Users } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { LeadTable } from "@/components/leads/lead-table";
import { LEAD_QUALIFICATION_LABELS } from "@/components/leads/qualification-badge";
import { LEAD_STATUS_LABELS, LEAD_STATUS_OPTIONS } from "@/components/leads/status-badge";
import { Button, EmptyState, Input, Select, SkeletonRows, Table, useToast } from "@/components/ui";
import { SESSION_TOKEN_KEY } from "@/lib/api";
import { downloadCsv } from "@/lib/download-csv";
import { useLeads } from "@/lib/hooks";
import type { LeadQualificationStatus, LeadStatus } from "@/lib/types";

interface ImportLeadsResult {
  campaign: { name: string; channel: string };
  campaigns: { name: string; channel: string }[];
  imported: number;
  skipped: number;
  errors: { row: number; reason: string }[];
}

type LeadImportStartMode = "draft" | "now" | "scheduled";

const INTENT_SEARCH_DEBOUNCE_MS = 300;

// status and qualification_status are independently editable (the lead
// table's Qualification dropdown writes qualification_status only, never
// touching status — see lead-table.tsx) — a lead marked "Qualified" there
// commonly still has status "new"/"contacted". So "qualification_status:
// qualified" is NOT redundant with the "status: qualified" option above it,
// despite sharing a label — omitting it (as this used to) meant leads
// qualified via that workflow had no way to be filtered for at all.
const EXTRA_QUALIFICATION_FILTER_OPTIONS: LeadQualificationStatus[] = [
  "qualified",
  "in_review",
  "disqualified",
];

async function downloadLeadsCsv(channel?: "voice" | "whatsapp", search?: string): Promise<void> {
  const params = new URLSearchParams();
  if (channel) params.set("channel", channel);
  if (search) params.set("search", search);
  const qs = params.toString();
  const stamp = new Date().toISOString().slice(0, 10);
  await downloadCsv(`/admin/leads.csv${qs ? `?${qs}` : ""}`, `leads-${stamp}.csv`);
}

async function downloadSampleImportFile(format: "csv" | "xlsx"): Promise<void> {
  await downloadCsv(`/admin/leads/sample.${format}`, `leads-sample.${format}`);
}

/**
 * Upload a CSV or Excel (.xlsx) file of leads to `POST /admin/leads/import`
 * (same auth pattern as `downloadLeadsCsv` above — plain `fetch` with the
 * session token header, since this is a one-off outside the
 * `apiFetch`/react-query flow; apiFetch would force a JSON Content-Type onto
 * what has to be multipart/form-data). The backend sniffs the file extension
 * to pick a CSV or Excel parser.
 */
async function importLeadsFile(
  file: File,
  channel?: "voice" | "whatsapp",
  startMode: LeadImportStartMode = "draft",
  scheduledStartAt?: string
): Promise<ImportLeadsResult> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem(SESSION_TOKEN_KEY) ?? "";

  const headers: Record<string, string> = {};
  if (token) headers["X-Session-Token"] = token;

  const qs = channel ? `?channel=${encodeURIComponent(channel)}` : "";
  const form = new FormData();
  form.append("file", file);
  form.append("start_mode", startMode);
  if (scheduledStartAt) form.append("scheduled_start_at", scheduledStartAt);

  const res = await fetch(`${base}/admin/leads/import${qs}`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    throw new Error(`Import failed (${res.status} ${res.statusText})`);
  }
  return res.json();
}

export interface LeadsViewProps {
  title: string;
  description: string;
  /** Scopes the leads list (and CSV export) to a single channel. */
  channel?: "voice" | "whatsapp";
  /** Base path for row navigation, e.g. "/whatsapp/leads". Rows are inert without it. */
  detailBasePath?: string;
}

/**
 * Leads list + CSV export (UI plan §7.2). Shared by the unified Leads page
 * and the per-channel /whatsapp/leads and /calling/leads pages.
 */
export function LeadsView({ title, description, channel, detailBasePath }: LeadsViewProps) {
  const searchParams = useSearchParams();
  const initialChannelParam = searchParams.get("channel");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  // Combined status/qualification filter — a single dropdown that reads from
  // whichever field the chosen option belongs to. Encoded as "status:<value>"
  // or "qual:<value>" so one Select can drive two separate Lead fields
  // without showing two near-duplicate dropdowns (both have a "Qualified"
  // option). Only the qualification stages not already implied by a status
  // value are offered here — "Unqualified" ~= "New" and "Qualified" already
  // exists as a status.
  const [stageFilter, setStageFilter] = useState("");
  const status = stageFilter.startsWith("status:")
    ? (stageFilter.slice("status:".length) as LeadStatus)
    : "";
  const qualificationFilter = stageFilter.startsWith("qual:")
    ? (stageFilter.slice("qual:".length) as LeadQualificationStatus)
    : "";
  const [channelFilter, setChannelFilter] = useState<"voice" | "whatsapp" | "">(
    initialChannelParam === "voice" || initialChannelParam === "whatsapp" ? initialChannelParam : ""
  );
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importStartMode, setImportStartMode] = useState<LeadImportStartMode>("draft");
  const [importScheduledAt, setImportScheduledAt] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();

  // Only the unified (cross-channel) view lets the user pick a channel —
  // per-channel pages already have `channel` fixed by their caller.
  const effectiveChannel = channel ?? (channelFilter || undefined);

  // Single search box matches against intent (a freeform sentence captured
  // by the LLM, e.g. "Book an appointment on July 8th") OR tags — so this is
  // a debounced substring search rather than an exact-match dropdown. See
  // apps/api/routers/admin.py's _lead_search_clause().
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), INTENT_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  const filters = {
    channel: effectiveChannel,
    ...(status ? { status } : {}),
    ...(qualificationFilter ? { qualification_status: qualificationFilter } : {}),
    ...(search ? { search } : {}),
  };
  const { data, isLoading, isError, error, refetch } = useLeads(filters);
  const leads = data ?? [];

  async function handleExport() {
    setExporting(true);
    try {
      await downloadLeadsCsv(effectiveChannel, search || undefined);
      toast({ title: "Export started", description: "Your CSV download is ready.", variant: "success" });
    } catch (err: unknown) {
      toast({
        title: "Export failed",
        description: err instanceof Error ? err.message : "Could not export leads.",
        variant: "error",
      });
    } finally {
      setExporting(false);
    }
  }

  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file next time
    if (!file) return;

    if (importStartMode === "scheduled" && !importScheduledAt) {
      toast({
        title: "Pick a date and time",
        description: "Choose when this campaign should start, or switch to Draft/Start now.",
        variant: "error",
      });
      return;
    }

    setImporting(true);
    try {
      const scheduledIso =
        importStartMode === "scheduled" ? new Date(importScheduledAt).toISOString() : undefined;
      const result = await importLeadsFile(file, effectiveChannel, importStartMode, scheduledIso);
      // Leads-page import creates a Lead per contact immediately (status
      // "qualified", editable like any lead) — unlike the Campaigns page,
      // which only creates a Lead once the AI actually qualifies someone.
      // It also still stages the usual campaign for AI outreach in the
      // background (routers/admin.py::_create_campaigns_from_rows,
      // auto_qualify=True) — one campaign per upload, even for a mixed
      // call+WhatsApp file. Outreach only begins once the campaign is
      // started — see importStartMode.
      const campaignName = `"${result.campaign.name}"`;
      const outreachText =
        importStartMode === "now"
          ? `The AI is now reaching out via ${campaignName}.`
          : importStartMode === "scheduled"
            ? `Outreach via ${campaignName} is scheduled to begin at the time you chose.`
            : `Outreach via ${campaignName} is saved as a draft — start it from the Campaigns page when ready.`;
      toast({
        title: "Import complete",
        description:
          result.skipped > 0
            ? `Added ${result.imported} lead(s), marked Qualified (${result.skipped} row(s) skipped). ${outreachText}`
            : `Added ${result.imported} lead(s), marked Qualified. ${outreachText}`,
        variant: result.skipped > 0 ? "info" : "success",
      });
      await refetch();
    } catch (err: unknown) {
      toast({
        title: "Import failed",
        description: err instanceof Error ? err.message : "Could not import leads.",
        variant: "error",
      });
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title={title}
        description={description}
        action={
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search
                size={14}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <Input
                id="lead-search"
                type="search"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search intent or tag…"
                aria-label="Search leads by intent or tag"
                className="w-48 pl-8 sm:w-56"
              />
            </div>
            {channel === undefined && (
              <Select
                value={channelFilter}
                onChange={(v) => setChannelFilter(v as "voice" | "whatsapp" | "")}
                aria-label="Filter leads by channel"
              >
                <option value="">All channels</option>
                <option value="voice">Call leads</option>
                <option value="whatsapp">WhatsApp leads</option>
              </Select>
            )}
            <Select
              value={stageFilter}
              onChange={(v) => setStageFilter(v)}
              aria-label="Filter leads by status"
            >
              <option value="">All statuses</option>
              {LEAD_STATUS_OPTIONS.map((s) => (
                <option key={s} value={`status:${s}`}>
                  {LEAD_STATUS_LABELS[s]}
                </option>
              ))}
              {EXTRA_QUALIFICATION_FILTER_OPTIONS.map((s) => (
                <option key={s} value={`qual:${s}`}>
                  Qualification: {LEAD_QUALIFICATION_LABELS[s]}
                </option>
              ))}
            </Select>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx"
              className="hidden"
              onChange={handleImportFile}
            />
            <Button
              variant="ghost"
              size="md"
              onClick={() => downloadSampleImportFile("csv")}
              title="Download a sample CSV showing the expected import columns"
            >
              <FileSpreadsheet size={15} aria-hidden />
              Sample CSV
            </Button>
            <Button
              variant="ghost"
              size="md"
              onClick={() => downloadSampleImportFile("xlsx")}
              title="Download a sample Excel file showing the expected import columns"
            >
              <FileSpreadsheet size={15} aria-hidden />
              Sample XLSX
            </Button>
            <Select
              value={importStartMode}
              onChange={(v) => setImportStartMode(v as LeadImportStartMode)}
              aria-label="When to start outreach for imported leads"
            >
              <option value="draft">Save as draft</option>
              <option value="now">Start now</option>
              <option value="scheduled">Schedule for…</option>
            </Select>
            {importStartMode === "scheduled" && (
              <input
                type="datetime-local"
                value={importScheduledAt}
                onChange={(e) => setImportScheduledAt(e.target.value)}
                aria-label="Scheduled start date and time"
                className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
            )}
            <Button
              variant="outline"
              size="md"
              loading={importing}
              onClick={() => fileInputRef.current?.click()}
              disabled={importing}
            >
              {!importing && <Upload size={15} aria-hidden />}
              Import Leads
            </Button>
            <Button
              variant="outline"
              size="md"
              loading={exporting}
              onClick={handleExport}
              disabled={exporting || leads.length === 0}
            >
              {!exporting && <Download size={15} aria-hidden />}
              Export CSV
            </Button>
          </div>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={leads.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={5} cols={6} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState
            icon={Users}
            title="No leads yet"
            description="Leads will appear when the agent captures contact info."
          />
        }
      >
        <LeadTable leads={leads} detailBasePath={detailBasePath} />
      </QueryBoundary>
    </div>
  );
}
