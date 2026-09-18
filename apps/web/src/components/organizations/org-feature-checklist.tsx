"use client";

import { Input, Label } from "@/components/ui";
import { ORG_FEATURES } from "@/lib/orgFeatures";

/**
 * Platform-admin checklist of which product modules an org may use, plus a
 * max-team-members cap. `enabledFeatures === null` means "unrestricted" (the
 * default for every org) — toggling any checkbox off switches to an
 * explicit list from that point on.
 */
export function OrgFeatureChecklist({
  idPrefix,
  enabledFeatures,
  onChangeFeatures,
  maxTeamMembers,
  onChangeMaxTeamMembers,
}: {
  idPrefix: string;
  enabledFeatures: string[] | null;
  onChangeFeatures: (next: string[] | null) => void;
  maxTeamMembers: string;
  onChangeMaxTeamMembers: (next: string) => void;
}) {
  const isEnabled = (key: string) => enabledFeatures === null || enabledFeatures.includes(key);

  function toggle(key: string, checked: boolean) {
    const current = enabledFeatures ?? ORG_FEATURES.map((f) => f.key);
    const next = checked ? [...current, key] : current.filter((k) => k !== key);
    onChangeFeatures(next.length === ORG_FEATURES.length ? null : next);
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <span className="text-sm font-medium">Enabled features</span>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {ORG_FEATURES.map((feature) => (
            <label
              key={feature.key}
              htmlFor={`${idPrefix}-feature-${feature.key}`}
              className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200"
            >
              <input
                id={`${idPrefix}-feature-${feature.key}`}
                type="checkbox"
                checked={isEnabled(feature.key)}
                onChange={(e) => toggle(feature.key, e.target.checked)}
                className="rounded border-slate-300 dark:border-slate-600"
              />
              {feature.label}
            </label>
          ))}
        </div>
      </div>
      <div>
        <Label htmlFor={`${idPrefix}-max-team-members`}>Max team members</Label>
        <Input
          id={`${idPrefix}-max-team-members`}
          type="number"
          min={1}
          value={maxTeamMembers}
          onChange={(e) => onChangeMaxTeamMembers(e.target.value.replace(/[^\d]/g, ""))}
          placeholder="Unlimited"
        />
        <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
          Leave blank for no limit on how many team members this org can invite.
        </p>
      </div>
    </div>
  );
}
