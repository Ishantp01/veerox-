"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";

import { Badge, Button, Input, Label } from "@/components/ui";
import { E164_MESSAGE, E164_REGEX } from "@/lib/phone";

export interface PhoneNumberEntry {
  phone_number: string;
  is_default: boolean;
}

export interface PhoneNumberListFieldProps {
  id: string;
  label: string;
  value: PhoneNumberEntry[];
  onChange: (next: PhoneNumberEntry[]) => void;
}

/**
 * One provider's list of dedicated numbers — add/remove entries, mark one
 * "Primary" (the number outbound calls dial from; see
 * apps/api/channels/voice/org_numbers.py::get_default_numbers). The first
 * number added becomes Primary automatically; removing the Primary entry
 * promotes whichever is left at the top of the list.
 */
export function PhoneNumberListField({ id, label, value, onChange }: PhoneNumberListFieldProps) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | undefined>();

  function handleAdd() {
    const trimmed = draft.trim();
    if (!E164_REGEX.test(trimmed)) {
      setError(E164_MESSAGE);
      return;
    }
    if (value.some((entry) => entry.phone_number === trimmed)) {
      setError("This number is already in the list");
      return;
    }
    onChange([...value, { phone_number: trimmed, is_default: value.length === 0 }]);
    setDraft("");
    setError(undefined);
  }

  function handleRemove(index: number) {
    const removed = value[index];
    const next = value.filter((_, i) => i !== index);
    if (removed.is_default && next.length > 0) {
      next[0] = { ...next[0], is_default: true };
    }
    onChange(next);
  }

  function handleSetPrimary(index: number) {
    onChange(value.map((entry, i) => ({ ...entry, is_default: i === index })));
  }

  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      {value.length > 0 && (
        <div className="mb-2 flex flex-col gap-1.5">
          {value.map((entry, i) => (
            <div
              key={entry.phone_number}
              className="flex items-center gap-2 rounded-md border border-slate-200 px-2.5 py-1.5 dark:border-slate-800"
            >
              <span className="flex-1 font-mono text-xs text-slate-700 dark:text-slate-300">
                {entry.phone_number}
              </span>
              {entry.is_default ? (
                <Badge variant="success" icon={null}>
                  Primary
                </Badge>
              ) : (
                value.length > 1 && (
                  <button
                    type="button"
                    onClick={() => handleSetPrimary(i)}
                    className="text-xs font-medium text-primary-600 hover:underline dark:text-primary-400"
                  >
                    Set primary
                  </button>
                )
              )}
              <button
                type="button"
                aria-label={`Remove ${entry.phone_number}`}
                onClick={() => handleRemove(i)}
                className="text-slate-400 transition-colors hover:text-red-600"
              >
                <X size={14} aria-hidden />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          id={id}
          type="tel"
          inputMode="tel"
          maxLength={16}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value.replace(/[^\d+]/g, ""));
            setError(undefined);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleAdd();
            }
          }}
          placeholder="+91XXXXXXXXXX"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        <Button type="button" variant="outline" size="sm" onClick={handleAdd}>
          <Plus size={14} aria-hidden />
          Add
        </Button>
      </div>
      {error && (
        <p id={`${id}-error`} className="mt-1.5 text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
