"use client";

import { useEffect, useRef, useState } from "react";
import { Search, User, X } from "lucide-react";
import { Input } from "@/components/ui";
import { useContacts } from "@/lib/hooks";
import { formatPhone } from "@/lib/format";
import type { Contact } from "@/lib/types";

const SEARCH_DEBOUNCE_MS = 250;

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
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [qInput]);

  const { data, isLoading } = useContacts(q || undefined);
  const results = q ? (data ?? []) : [];

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
            <p className="px-3.5 py-3 text-xs text-slate-400">No contacts found.</p>
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
