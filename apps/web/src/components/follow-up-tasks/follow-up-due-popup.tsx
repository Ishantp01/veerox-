"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, BellRing, Check, Clock, X } from "lucide-react";

import { playAlertSound } from "@/components/escalations/emergency-escalation-popup";
import { Button, useToast } from "@/components/ui";
import { formatDateTime, formatPhone } from "@/lib/format";
import { followUpKey, useLeadFollowUps, useUpdateLeadFollowUp, type LeadFollowUp } from "@/lib/hooks";

const SNOOZE_MS = 10 * 60 * 1000;
const STORAGE_KEY = "veerox:follow-up-snoozes";

/** id -> epoch ms the snooze ends. The id embeds the due time, so
 * rescheduling a follow-up makes it a new id and clears any old snooze. */
type Snoozes = Record<string, number>;

function popupId(f: LeadFollowUp): string {
  return `${followUpKey(f)}@${f.follow_up_at}`;
}

function loadSnoozes(): Snoozes {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Snoozes) : {};
    const now = Date.now();
    return Object.fromEntries(Object.entries(parsed).filter(([, until]) => until > now));
  } catch {
    return {};
  }
}

function saveSnoozes(s: Snoozes) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // Storage can be blocked (private window) — the snooze just won't survive a reload.
  }
}

function notifyBrowser(f: LeadFollowUp) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  if (document.visibilityState !== "hidden") return;
  try {
    new Notification(`Follow-up ${f.slot} due`, {
      body: `${f.lead_name ?? formatPhone(f.lead_phone)}${f.note ? ` — ${f.note}` : ""}`,
      tag: popupId(f),
    });
  } catch {
    // Notification constructor can throw in some embedded/webview contexts.
  }
}

/**
 * Corner reminder for lead follow-ups whose time has arrived, mounted once in
 * DashboardShell so it reaches every dashboard route. The server already
 * scopes /lead-follow-ups to the caller: org admins/owners get every due
 * follow-up in the org, team members get the ones on leads they claimed.
 *
 * Stays up until the follow-up is marked done (which clears its date) or
 * snoozed for 10 minutes; snoozes are kept per browser.
 */
export function FollowUpDuePopup() {
  const { data } = useLeadFollowUps({ due: true });
  const update = useUpdateLeadFollowUp();
  const { toast } = useToast();
  const router = useRouter();

  const [snoozes, setSnoozes] = useState<Snoozes>({});
  const [, setTick] = useState(0);
  const alerted = useRef<Set<string>>(new Set());

  useEffect(() => {
    setSnoozes(loadSnoozes());
    // Re-evaluate every 30s so an expired snooze resurfaces on its own.
    const t = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  const now = Date.now();
  const visible = (data ?? []).filter((f) => (snoozes[popupId(f)] ?? 0) <= now);
  const active = visible[0];

  const activeId = active ? popupId(active) : null;
  useEffect(() => {
    if (!active || !activeId || alerted.current.has(activeId)) return;
    alerted.current.add(activeId);
    playAlertSound();
    notifyBrowser(active);
  }, [active, activeId]);

  if (!active) return null;

  function snooze() {
    const next = { ...snoozes, [popupId(active!)]: Date.now() + SNOOZE_MS };
    setSnoozes(next);
    saveSnoozes(next);
    // Let it resurface once the snooze window passes.
    setTimeout(() => setTick((n) => n + 1), SNOOZE_MS + 50);
  }

  function markDone() {
    update.mutate(
      { leadId: active!.lead_id, slot: active!.slot, at: null },
      {
        onSuccess: () => toast({ title: "Follow-up marked done", variant: "success" }),
        onError: (err) =>
          toast({ title: "Couldn't update", description: err.message, variant: "error" }),
      },
    );
  }

  const title = active.lead_name || formatPhone(active.lead_phone);

  return (
    <div
      role="alert"
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-50 w-[calc(100%-2rem)] max-w-sm"
    >
      <div className="pointer-events-auto relative animate-fade-up overflow-hidden rounded-2xl border border-amber-200 bg-white shadow-card-lg dark:border-amber-500/30 dark:bg-slate-900">
        <span className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-amber-400 to-amber-500" aria-hidden />
        <button
          type="button"
          onClick={snooze}
          aria-label="Snooze for 10 minutes"
          className="absolute right-3 top-3 rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:hover:bg-slate-800 dark:hover:text-slate-300"
        >
          <X size={16} aria-hidden />
        </button>

        <div className="px-4 pb-4 pt-5">
          <div className="flex items-start gap-2.5 pr-6">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300">
              <BellRing size={16} aria-hidden />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Follow-up {active.slot} is due
                {visible.length > 1 && (
                  <span className="ml-1.5 text-xs font-medium text-slate-400">
                    +{visible.length - 1} more
                  </span>
                )}
              </p>
              <p className="mt-0.5 truncate text-sm text-slate-700 dark:text-slate-300">{title}</p>
              {active.lead_name && active.lead_phone && (
                <p className="font-mono text-xs text-slate-500">{formatPhone(active.lead_phone)}</p>
              )}
              {active.note && (
                <p className="mt-1.5 line-clamp-3 text-xs text-slate-600 dark:text-slate-400">
                  {active.note}
                </p>
              )}
              <p className="mt-1.5 flex items-center gap-1 text-xs text-slate-400">
                <Clock size={12} aria-hidden /> Scheduled {formatDateTime(active.follow_up_at)}
              </p>
            </div>
          </div>

          <div className="mt-3.5 flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => router.push(`/crm/leads/${active.lead_id}`)}
            >
              Open lead <ArrowRight size={14} aria-hidden />
            </Button>
            <Button size="sm" variant="outline" onClick={markDone} loading={update.isPending}>
              <Check size={14} aria-hidden /> Done
            </Button>
            <Button size="sm" variant="ghost" onClick={snooze}>
              Snooze 10m
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
