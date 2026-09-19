"use client";

import { useState } from "react";
import { Input, Label, Select } from "@/components/ui";
import { COUNTRY_CODE_OPTIONS } from "@/lib/phone";

const OTHER = "__other__";

export interface CountryCodeFieldProps {
  id: string;
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  label?: string;
}

/**
 * Country-code picker: a long list of countries plus "Other" for any dialing
 * prefix not listed (typed as digits, stored as "+NN"). Shared by the org
 * create/edit dialogs and the org Settings page.
 */
export function CountryCodeField({
  id,
  value,
  onChange,
  disabled,
  label = "Country code",
}: CountryCodeFieldProps) {
  const known = COUNTRY_CODE_OPTIONS.some((o) => o.code === value);
  const [custom, setCustom] = useState(!known);
  const [draft, setDraft] = useState(known ? "" : value.replace(/\D/g, ""));
  const showCustom = custom || !known;

  function commit(raw: string) {
    const digits = raw.replace(/\D/g, "").slice(0, 4);
    setDraft(digits);
    if (digits) onChange(`+${digits}`);
  }

  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <Select
        id={id}
        value={showCustom ? OTHER : value}
        disabled={disabled}
        onChange={(v) => {
          if (v === OTHER) {
            setCustom(true);
            return;
          }
          setCustom(false);
          onChange(v);
        }}
        className="w-full"
      >
        {COUNTRY_CODE_OPTIONS.map((o) => (
          <option key={o.code + o.label} value={o.code}>
            {o.label}
          </option>
        ))}
        <option value={OTHER}>Other (enter code)…</option>
      </Select>
      {showCustom && (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-500">+</span>
          <Input
            id={`${id}-custom`}
            inputMode="numeric"
            value={draft}
            onChange={(e) => commit(e.target.value)}
            placeholder="e.g. 98"
            maxLength={4}
            aria-label="Custom country code digits"
            className="w-28 font-mono"
          />
        </div>
      )}
    </div>
  );
}
