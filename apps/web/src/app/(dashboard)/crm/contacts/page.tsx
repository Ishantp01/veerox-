"use client";

import { useEffect, useRef, useState } from "react";
import { FileSpreadsheet, Search, Upload, Users } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ContactTable } from "@/components/crm/contact-table";
import { NewContactDialog } from "@/components/crm/new-contact-dialog";
import { Button, EmptyState, Input, Pagination, SkeletonRows, Table, useToast } from "@/components/ui";
import { SESSION_TOKEN_KEY } from "@/lib/api";
import { downloadCsv } from "@/lib/download-csv";
import { useClientPagination, useContacts } from "@/lib/hooks";

const SEARCH_DEBOUNCE_MS = 300;

interface ImportContactsResult {
  imported: number;
  updated: number;
  skipped: number;
  errors: { row: number; reason: string }[];
}

async function downloadSampleContactsFile(format: "csv" | "xlsx"): Promise<void> {
  await downloadCsv(`/crm/contacts/sample.${format}`, `contacts-sample.${format}`);
}

/**
 * Upload a CSV or Excel (.xlsx) file of contacts to `POST /crm/contacts/import`
 * — plain `fetch` with the session token header (mirrors leads-view.tsx's
 * importLeadsFile), since apiFetch forces a JSON Content-Type unsuited to
 * multipart/form-data.
 */
async function importContactsFile(file: File): Promise<ImportContactsResult> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem(SESSION_TOKEN_KEY) ?? "";

  const headers: Record<string, string> = {};
  if (token) headers["X-Session-Token"] = token;

  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${base}/crm/contacts/import`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    throw new Error(`Import failed (${res.status} ${res.statusText})`);
  }
  return res.json();
}

export default function ContactsPage() {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [qInput]);

  const { data, isLoading, isError, error, refetch } = useContacts(q || undefined);
  const contacts = data ?? [];
  const pager = useClientPagination(contacts, 20, q);

  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImporting(true);
    try {
      const result = await importContactsFile(file);
      await refetch();
      const parts = [`Added ${result.imported}`];
      if (result.updated > 0) parts.push(`updated ${result.updated}`);
      if (result.skipped > 0) parts.push(`skipped ${result.skipped}`);
      toast({
        title: "Import complete",
        description: `${parts.join(", ")} contact(s).`,
        variant: "success",
      });
    } catch (err) {
      toast({
        title: "Import failed",
        description: err instanceof Error ? err.message : "Could not import contacts.",
        variant: "error",
      });
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Contacts"
        description="Unified CRM contacts, shared across the calling and WhatsApp channels."
        action={
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search
                size={14}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <Input
                type="search"
                value={qInput}
                onChange={(e) => setQInput(e.target.value)}
                placeholder="Search name or phone…"
                aria-label="Search contacts"
                className="w-48 pl-8 sm:w-56"
              />
            </div>
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
              onClick={() => downloadSampleContactsFile("csv")}
              title="Download a sample CSV showing the expected import columns"
            >
              <FileSpreadsheet size={15} aria-hidden />
              Sample CSV
            </Button>
            <Button
              variant="ghost"
              size="md"
              onClick={() => downloadSampleContactsFile("xlsx")}
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
              Import Contacts
            </Button>
            <NewContactDialog />
          </div>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={contacts.length === 0}
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
            title="No contacts yet"
            description="Create your first contact, or leads will populate this list as they're linked."
          />
        }
      >
        <ContactTable contacts={pager.pageRows} detailBasePath="/crm/contacts" />
        <Pagination
          page={pager.page}
          pageSize={pager.pageSize}
          rowCount={pager.rowCount}
          hasNextPage={pager.hasNextPage}
          onPrev={pager.onPrev}
          onNext={pager.onNext}
        />
      </QueryBoundary>
    </div>
  );
}
