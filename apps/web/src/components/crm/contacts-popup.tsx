"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, User, Users } from "lucide-react";
import {
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogTitle,
  DialogTrigger,
  Input,
} from "@/components/ui";
import { formatPhone } from "@/lib/format";
import { useContacts } from "@/lib/hooks";

const SEARCH_DEBOUNCE_MS = 250;

/**
 * Quick-glance popup listing every CRM contact, opened from the Leads page
 * so a rep can check the contacts list without leaving it. Read-only —
 * picking a contact navigates to its detail page (/crm/contacts/[id]),
 * mirroring contact-table.tsx's row-click behavior.
 */
export function ContactsPopup() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [qInput]);

  const { data, isLoading } = useContacts(q || undefined);
  const contacts = data ?? [];

  function openContact(id: string) {
    setOpen(false);
    router.push(`/crm/contacts/${id}`);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="outline" size="md" title="View all contacts">
          <Users size={15} aria-hidden />
          Contacts
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Contacts</DialogTitle>
        <DialogBody className="flex flex-col gap-3">
          <div className="relative">
            <Search
              size={14}
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <Input
              autoFocus
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Search name or phone…"
              aria-label="Search contacts"
              className="pl-8"
            />
          </div>
          {isLoading ? (
            <p className="py-4 text-center text-xs text-slate-400">Loading…</p>
          ) : contacts.length === 0 ? (
            <p className="py-4 text-center text-xs text-slate-400">
              {q ? `No contacts found for "${q}".` : "No contacts yet."}
            </p>
          ) : (
            <ul className="-mx-2 max-h-72 overflow-y-auto">
              {contacts.map((contact) => (
                <li key={contact.id}>
                  <button
                    type="button"
                    onClick={() => openContact(contact.id)}
                    className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm hover:bg-primary-50/60 dark:hover:bg-primary-500/10"
                  >
                    <User size={14} className="shrink-0 text-slate-400" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-slate-800 dark:text-slate-100">
                      {contact.name ?? formatPhone(contact.phone)}
                    </span>
                    <span className="shrink-0 font-mono text-xs text-slate-400">
                      {formatPhone(contact.phone)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
