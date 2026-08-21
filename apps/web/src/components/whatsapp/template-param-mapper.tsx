"use client";

import { Input, Label, Select } from "@/components/ui";

/**
 * A WhatsApp template's {{1}}, {{2}}, ... body placeholder can pull from a
 * per-contact field, a value resolved at the moment the message actually
 * sends, or a fixed piece of text the admin types once for the whole batch.
 * The resolved token strings (see TEMPLATE_PARAM_TOKENS) are what's sent to
 * the backend — apps/api/workers/whatsapp_dispatcher.py's
 * _resolve_template_body_params resolves them fresh at send time so
 * "send_date"/"send_time" reflect when the message actually goes out, not
 * when the campaign was created.
 */
export type TemplateParamSource = "name" | "date" | "time" | "custom";

export const TEMPLATE_PARAM_TOKENS: Record<Exclude<TemplateParamSource, "custom">, string> = {
  name: "{{contact_name}}",
  date: "{{send_date}}",
  time: "{{send_time}}",
};

/** Best-effort default source per param label, e.g. a label containing
 * "name" defaults to the contact-name source — the admin can still change
 * it. Falls back to "custom" (a fixed value they type in). */
export function guessTemplateParamSource(label: string): TemplateParamSource {
  const l = label.toLowerCase();
  if (l.includes("name")) return "name";
  if (l.includes("time")) return "time";
  if (l.includes("date")) return "date";
  return "custom";
}

export function resolveTemplateParams(
  paramLabels: string[],
  sources: TemplateParamSource[],
  customValues: string[]
): string[] {
  return paramLabels.map((_, i) => {
    const source = sources[i] ?? "custom";
    return source === "custom" ? (customValues[i] ?? "") : TEMPLATE_PARAM_TOKENS[source];
  });
}

export interface TemplateParamMapperProps {
  paramLabels: string[];
  sources: TemplateParamSource[];
  customValues: string[];
  onSourceChange: (index: number, source: TemplateParamSource) => void;
  onCustomValueChange: (index: number, value: string) => void;
  /** Label for the "pull from contact" option — differs slightly by page
   * (a campaign's contacts come from an uploaded file; a follow-up rule's
   * contact is the matched lead). */
  nameSourceLabel?: string;
}

export function TemplateParamMapper({
  paramLabels,
  sources,
  customValues,
  onSourceChange,
  onCustomValueChange,
  nameSourceLabel = "Contact name (from file)",
}: TemplateParamMapperProps) {
  if (paramLabels.length === 0) return null;

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900/50">
      <p className="text-xs font-semibold text-slate-600 dark:text-slate-300">
        Fill this template&apos;s {paramLabels.length} placeholder{paramLabels.length > 1 ? "s" : ""}
      </p>
      {paramLabels.map((label, i) => {
        const source = sources[i] ?? "custom";
        return (
          <div key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,160px)_1fr_1fr]">
            <Label className="mb-0 self-center text-xs text-slate-500 dark:text-slate-400">
              {`{{${i + 1}}}`} {label || `Param ${i + 1}`}
            </Label>
            <Select
              value={source}
              onChange={(v) => onSourceChange(i, v as TemplateParamSource)}
              className="w-full"
              aria-label={`Source for placeholder ${i + 1}`}
            >
              <option value="name">{nameSourceLabel}</option>
              <option value="date">Date message is sent</option>
              <option value="time">Time message is sent</option>
              <option value="custom">Custom text</option>
            </Select>
            {source === "custom" ? (
              <Input
                value={customValues[i] ?? ""}
                onChange={(e) => onCustomValueChange(i, e.target.value)}
                placeholder="Fixed value for this slot"
                aria-label={`Custom value for placeholder ${i + 1}`}
              />
            ) : (
              <span className="self-center text-xs text-slate-400 dark:text-slate-500">
                Filled automatically for each contact
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
