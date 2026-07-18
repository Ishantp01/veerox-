"use client";

import { AlertCircle, CheckCircle2, Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface KillSwitchBannerProps {
  /** True when the agent is paused (kill switch engaged). */
  enabled: boolean;
  /** Disables the toggle while a mutation is in flight. */
  loading?: boolean;
  /** Called with the next desired state when the operator toggles. */
  onToggle: (next: boolean) => void;
}

/**
 * Top-of-dashboard banner reflecting + controlling the global kill switch
 * (UI plan §5, §8.2). Red when paused, calm slate/emerald when live. Announced
 * politely so screen readers hear state changes (a11y §10).
 */
export function KillSwitchBanner({
  enabled,
  loading = false,
  onToggle,
}: KillSwitchBannerProps) {
  return (
    <div
      aria-live="polite"
      className={cn(
        "mb-6 flex flex-col gap-3 rounded-2xl border px-5 py-4 sm:flex-row sm:items-center sm:justify-between",
        enabled
          ? "border-red-500/40 bg-gradient-to-r from-red-600 to-rose-600 text-white shadow-card-lg"
          : "border-slate-200/80 bg-white text-slate-700 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
      )}
    >
      <div className="flex items-center gap-3">
        {enabled ? (
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/15">
            <AlertCircle size={18} aria-hidden />
          </span>
        ) : (
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-emerald-600 text-white">
            <CheckCircle2 size={18} aria-hidden />
          </span>
        )}
        <div>
          <p className="text-sm font-bold">
            {enabled ? "Agent is PAUSED" : "Agent is live"}
          </p>
          <p className={cn("text-xs", enabled ? "text-red-100" : "text-slate-500 dark:text-slate-400")}>
            {enabled
              ? "Incoming messages receive a hold response until you resume."
              : "Voice and WhatsApp messages are being answered automatically."}
          </p>
        </div>
      </div>
      <Button
        variant={enabled ? "secondary" : "danger"}
        size="sm"
        loading={loading}
        onClick={() => onToggle(!enabled)}
      >
        {!loading &&
          (enabled ? (
            <Play size={14} aria-hidden />
          ) : (
            <Pause size={14} aria-hidden />
          ))}
        {enabled ? "Resume agent" : "Pause agent"}
      </Button>
    </div>
  );
}

export default KillSwitchBanner;
