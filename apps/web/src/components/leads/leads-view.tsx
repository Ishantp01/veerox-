"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Download, FileSpreadsheet, Search, Upload, Users } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { LeadTable } from "@/components/leads/lead-table";
import { LEAD_STATUS_LABELS, LEAD_STATUS_OPTIONS } from "@/components/leads/status-badge";
import { Button, EmptyState, Input, SkeletonRows, Table, useToast } from "@/components/ui";
import { downloadCsv } from "@/lib/download-csv";
import { useLeads } from "@/lib/hooks";
import type { LeadStatus } from "@/lib/types";

interface ImportLeadsResult {
  imported: number;
  skipped: number;
  errors: { row: number; reason: string }[];
}

const TOKEN_KEY = "veerox_admin_token";
const INTENT_SEARCH_DEBOUNCE_MS = 300;

async function downloadLeadsCsv(channel?: "voice" | "whatsapp"): Promise<void> {
  const qs = channel ? `?channel=${encodeURIComponent(channel)}` : "";
  const stamp = new Date().toISOString().slice(0, 10);
  await downloadCsv(`/admin/leads.csv${qs}`, `leads-${stamp}.csv`);
}

async function downloadSampleImportFile(format: "csv" | "xlsx"): Promise<void> {
  await downloadCsv(`/admin/leads/sample.${format}`, `leads-sample.${format}`);
}

/**
 * Upload a CSV or Excel (.xlsx) file of leads to `POST /admin/leads/import`
 * (same auth pattern as `downloadLeadsCsv` above — plain `fetch` with the
 * admin token header, since this is a one-off outside the
 * `apiFetch`/react-query flow). The backend sniffs the file extension to
 * pick a CSV or Excel parser.
 */
async function importLeadsFile(
  file: File,
  channel?: "voice" | "whatsapp"
): Promise<ImportLeadsResult> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem(TOKEN_KEY) ?? "";

  const headers: Record<string, string> = {};
  if (token) headers["X-Admin-Token"] = token;

  const qs = channel ? `?channel=${encodeURIComponent(channel)}` : "";
  const form = new FormData();
  form.append("file", file);

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
  const [intentInput, setIntentInput] = useState("");
  const [intent, setIntent] = useState("");
  const [status, setStatus] = useState<LeadStatus | "">("");
  const [channelFilter, setChannelFilter] = useState<"voice" | "whatsapp" | "">(
    initialChannelParam === "voice" || initialChannelParam === "whatsapp" ? initialChannelParam : ""
  );
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();

  // Only the unified (cross-channel) view lets the user pick a channel —
  // per-channel pages already have `channel` fixed by their caller.
  const effectiveChannel = channel ?? (channelFilter || undefined);

  // Intent is a freeform sentence captured by the LLM (e.g. "Book an
  // appointment on July 8th"), not a fixed category — so this is a debounced
  // substring search rather than an exact-match dropdown. See
  // apps/api/routers/admin.py's ilike() filter.
  useEffect(() => {
    const t = setTimeout(() => setIntent(intentInput.trim()), INTENT_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [intentInput]);

  const filters = { channel: effectiveChannel, ...(intent ? { intent } : {}), ...(status ? { status } : {}) };
  const { data, isLoading, isError, error, refetch } = useLeads(filters);
  const leads = data ?? [];

  async function handleExport() {
    setExporting(true);
    try {
      await downloadLeadsCsv(effectiveChannel);
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

    setImporting(true);
    try {
      const result = await importLeadsFile(file, effectiveChannel);
      toast({
        title: "Import complete",
        description:
          result.skipped > 0
            ? `Imported ${result.imported} lead(s), skipped ${result.skipped} row(s).`
            : `Imported ${result.imported} lead(s).`,
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
                id="intent-filter"
                type="search"
                value={intentInput}
                onChange={(e) => setIntentInput(e.target.value)}
                placeholder="Search intent…"
                aria-label="Search leads by intent"
                className="w-40 pl-8 sm:w-48"
              />
            </div>
            {channel === undefined && (
              <select
                value={channelFilter}
                onChange={(e) => setChannelFilter(e.target.value as "voice" | "whatsapp" | "")}
                aria-label="Filter leads by channel"
                className="rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              >
                <option value="">All channels</option>
                <option value="voice">Call leads</option>
                <option value="whatsapp">WhatsApp leads</option>
              </select>
            )}
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as LeadStatus | "")}
              aria-label="Filter leads by status"
              className="rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            >
              <option value="">All statuses</option>
              {LEAD_STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {LEAD_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
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
                <SkeletonRows rows={5} cols={5} />
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
