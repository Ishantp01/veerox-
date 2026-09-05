"use client";

import { useRouter } from "next/navigation";
import { Table, TableHeader, TableRow, TableCell } from "@/components/ui";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { Contact } from "@/lib/types";

export interface ContactTableProps {
  contacts: Contact[];
  detailBasePath: string;
}

/** Presentational contacts table — Name, Phone, Email, Company, Created. */
export function ContactTable({ contacts, detailBasePath }: ContactTableProps) {
  const router = useRouter();

  return (
    <div
      data-tour="page-table"
      className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900"
    >
      <Table>
        <thead>
          <TableRow isHeader>
            <TableHeader>Name</TableHeader>
            <TableHeader>Phone</TableHeader>
            <TableHeader>Email</TableHeader>
            <TableHeader>Company</TableHeader>
            <TableHeader>Created</TableHeader>
          </TableRow>
        </thead>
        <tbody>
          {contacts.map((contact) => {
            const href = `${detailBasePath}/${contact.id}`;
            return (
              <TableRow
                key={contact.id}
                role="link"
                tabIndex={0}
                aria-label={`Open contact ${contact.name ?? contact.phone}`}
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
                  <span className="font-semibold text-slate-800 dark:text-slate-100">
                    {contact.name ?? "—"}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs text-slate-600 dark:text-slate-400">
                    {formatPhone(contact.phone)}
                  </span>
                </TableCell>
                <TableCell className="text-xs text-slate-600 dark:text-slate-400">
                  {contact.email ?? "—"}
                </TableCell>
                <TableCell className="text-xs text-slate-600 dark:text-slate-400">
                  {contact.company ?? "—"}
                </TableCell>
                <TableCell className="text-xs text-slate-500">
                  {formatDateTime(contact.created_at)}
                </TableCell>
              </TableRow>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
