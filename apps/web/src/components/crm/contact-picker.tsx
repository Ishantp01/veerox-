"use client";

import { useEffect, useRef, useState } from "react";
import { Search, User, UserPlus, X } from "lucide-react";
import { Button, Input } from "@/components/ui";
import { useContacts, useCreateContact } from "@/lib/hooks";
import { formatPhone } from "@/lib/format";
import type { Contact } from "@/lib/types";

const SEARCH_DEBOUNCE_MS = 250;

// Same E.164-ish shape new-contact-dialog.tsx validates on the standalone
// "New Contact" form — kept identical so a number rejected there is
// rejected here too.
const PHONE_PATTERN = /^\+\d{8,15}$/;

export interface ContactPickerProps {
  value: Contact | null;
  onChange: (contact: Contact | null) => void;
  placeholder?: string;
}

/** Searchable contact combobox backed by GET /crm/contacts?q=. Shared by the
 * appointments booking form and the WhatsApp send page. */
export function ContactPicker({ value, onChange, placeholder }: ContactPickerProps) {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [addingNew, setAddingNew] = useState(false);
  const [newPhone, setNewPhone] = useState("");
  const [newName, setNewName] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const createContact = useCreateContact();

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [qInput]);

  function resetAddForm() {
    setAddingNew(false);
    setNewPhone("");
    setNewName("");
    setAddError(null);
  }

  useEffect(() => {
    resetAddForm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const { data, isLoading } = useContacts(q || undefined);
  const results = q ? (data ?? []) : [];

  function startAddNew() {
    setNewPhone(/^[+\d]/.test(q) ? q : "+91");
    setNewName(/^[+\d]/.test(q) ? "" : q);
    setAddError(null);
    setAddingNew(true);
  }

  function handleCreateNew() {
    const phone = newPhone.trim();
    if (!PHONE_PATTERN.test(phone)) {
      setAddError("Enter a valid E.164 number, e.g. +919876543210");
      return;
    }
    createContact.mutate(
      { name: newName.trim() || null, phone },
      {
        onSuccess: (contact) => {
          onChange(contact);
          setOpen(false);
          setQInput("");
          resetAddForm();
        },
        onError: (err) => setAddError(err.message),
      },
    );
  }

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm dark:border-slate-700 dark:bg-slate-900">
        <User size={15} className="shrink-0 text-slate-400" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-slate-800 dark:text-slate-100">
          {value.name ?? formatPhone(value.phone)}
          {value.name && <span className="ml-1.5 text-slate-400">{formatPhone(value.phone)}</span>}
        </span>
        <button
          type="button"
          onClick={() => onChange(null)}
          aria-label="Clear selected contact"
          className="shrink-0 rounded-md p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
        >
          <X size={14} aria-hidden />
        </button>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <Search
          size={14}
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
        />
        <Input
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          onFocus={() => setOpen(true)}
          placeholder={placeholder ?? "Search contacts by name or phone…"}
          className="pl-8"
        />
      </div>
      {open && q && (
        <div className="absolute z-10 mt-1 w-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card-lg dark:border-slate-800 dark:bg-slate-900">
          {isLoading ? (
            <p className="px-3.5 py-3 text-xs text-slate-400">Searching…</p>
          ) : results.length === 0 ? (
            <div className="p-3">
              {addingNew ? (
                <div className="flex flex-col gap-2">
                  <Input
                    autoFocus
                    type="tel"
                    inputMode="tel"
                    maxLength={16}
                    value={newPhone}
                    onChange={(e) => setNewPhone(e.target.value)}
                    placeholder="+91XXXXXXXXXX"
                  />
                  <Input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="Name (optional)"
                  />
                  {addError && <p className="text-xs text-red-600">{addError}</p>}
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="primary"
                      size="sm"
                      loading={createContact.isPending}
                      onClick={handleCreateNew}
                    >
                      Add &amp; select
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={resetAddForm}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <p className="text-xs text-slate-400">No contacts found for &quot;{q}&quot;.</p>
                  <button
                    type="button"
                    onClick={startAddNew}
                    className="flex items-center gap-1.5 self-start rounded-md px-1 py-0.5 text-xs font-medium text-primary-600 hover:bg-primary-50/60 dark:text-primary-400 dark:hover:bg-primary-500/10"
                  >
                    <UserPlus size={13} aria-hidden />
                    Add as new contact
                  </button>
                </div>
              )}
            </div>
          ) : (
            <ul className="max-h-56 overflow-y-auto">
              {results.map((contact) => (
                <li key={contact.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onChange(contact);
                      setOpen(false);
                      setQInput("");
                    }}
                    className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm hover:bg-primary-50/60 dark:hover:bg-primary-500/10"
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
        </div>
      )}
    </div>
  );
}
