"use client";

import { useEffect, useState } from "react";
import { Search, Users } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ContactTable } from "@/components/crm/contact-table";
import { NewContactDialog } from "@/components/crm/new-contact-dialog";
import { EmptyState, Input, SkeletonRows, Table } from "@/components/ui";
import { useContacts } from "@/lib/hooks";

const SEARCH_DEBOUNCE_MS = 300;

export default function ContactsPage() {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [qInput]);

  const { data, isLoading, isError, error, refetch } = useContacts(q || undefined);
  const contacts = data ?? [];

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
        <ContactTable contacts={contacts} detailBasePath="/crm/contacts" />
      </QueryBoundary>
    </div>
  );
}
