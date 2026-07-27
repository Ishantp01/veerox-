"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Building2, UserCheck } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import { IntentBadge } from "@/components/leads/intent-badge";
import { StatusBadge } from "@/components/leads/status-badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Skeleton,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { useContact } from "@/lib/hooks";
import { formatDateTime, formatPhone } from "@/lib/format";

function leadHref(channel: string | null, id: string): string {
  return channel === "voice" ? `/calling/leads/${id}` : `/whatsapp/leads/${id}`;
}

export interface ContactDetailProps {
  id: string;
}

/** Contact detail — profile fields plus every Lead rolled up under it, across channels. */
export function ContactDetail({ id }: ContactDetailProps) {
  const router = useRouter();
  const contact = useContact(id);

  return (
    <div className="mx-auto max-w-3xl">
      <Link
        href="/crm/contacts"
        className="mb-4 inline-flex items-center gap-1.5 rounded-md text-sm text-slate-500 transition-colors hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        <ArrowLeft size={15} aria-hidden />
        Contacts
      </Link>

      <QueryBoundary
        isLoading={contact.isLoading}
        isError={contact.isError}
        error={contact.error}
        onRetry={() => contact.refetch()}
        loadingFallback={<Skeleton className="h-64 w-full rounded-xl" />}
      >
        {contact.data && (
          <>
            <PageHeader
              title={contact.data.name ?? formatPhone(contact.data.phone)}
              description={formatPhone(contact.data.phone)}
            />

            <div className="flex flex-col gap-5">
              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Building2 size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Profile</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <dl className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <dt className="text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
                        Email
                      </dt>
                      <dd className="mt-1 text-slate-700 dark:text-slate-300">
                        {contact.data.email ?? "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
                        Company
                      </dt>
                      <dd className="mt-1 text-slate-700 dark:text-slate-300">
                        {contact.data.company ?? "—"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
                        Created
                      </dt>
                      <dd className="mt-1 text-slate-700 dark:text-slate-300">
                        {formatDateTime(contact.data.created_at)}
                      </dd>
                    </div>
                  </dl>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <UserCheck size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Leads</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  {contact.data.leads.length === 0 ? (
                    <EmptyState
                      icon={UserCheck}
                      title="No leads yet"
                      description="Leads captured for this contact's phone number will appear here."
                      className="border-0"
                    />
                  ) : (
                    <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
                      <Table>
                        <thead>
                          <TableRow isHeader>
                            <TableHeader>Channel</TableHeader>
                            <TableHeader>Intent</TableHeader>
                            <TableHeader>Status</TableHeader>
                            <TableHeader>Created</TableHeader>
                          </TableRow>
                        </thead>
                        <tbody>
                          {contact.data.leads.map((lead) => {
                            const href = leadHref(lead.channel, lead.id);
                            return (
                              <TableRow
                                key={lead.id}
                                role="link"
                                tabIndex={0}
                                aria-label={`Open lead ${lead.id}`}
                                onClick={() => router.push(href)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" || e.key === " ") {
                                    e.preventDefault();
                                    router.push(href);
                                  }
                                }}
                                className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
                              >
                                <TableCell>
                                  {lead.channel ? <ChannelBadge channel={lead.channel} /> : "—"}
                                </TableCell>
                                <TableCell>
                                  <IntentBadge intent={lead.intent} />
                                </TableCell>
                                <TableCell>
                                  <StatusBadge status={lead.status} />
                                </TableCell>
                                <TableCell className="text-xs text-slate-500">
                                  {formatDateTime(lead.created_at)}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </tbody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </QueryBoundary>
    </div>
  );
}
