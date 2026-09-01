"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowRight, BellRing, Clock, MessageSquare, Phone, X } from "lucide-react";
import { useToast } from "@/components/ui/toast";
import { formatPhone, formatRelative } from "@/lib/format";
import { useClaimEscalation, useEscalations } from "@/lib/hooks";
import type { Escalation } from "@/lib/types";
import { UrgencyBadge } from "./urgency-badge";

/** How long "Not now" hides an alert before it's eligible to resurface — it
 * is still unclaimed, so this is a snooze, not a dismissal. */
const SNOOZE_MS = 60 * 1000;

/** Two-tone alert beep via the Web Audio API — no audio asset to ship. */
function playAlertSound() {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const now = ctx.currentTime;
    [880, 660].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + i * 0.18);
      gain.gain.linearRampToValueAtTime(0.2, now + i * 0.18 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.18 + 0.16);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + i * 0.18);
      osc.stop(now + i * 0.18 + 0.2);
    });
    setTimeout(() => ctx.close(), 500);
  } catch {
    // Best-effort — a blocked/unsupported AudioContext shouldn't break the alert.
  }
}

function notifyBrowser(e: Escalation) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  if (document.visibilityState !== "hidden") return;
  try {
    new Notification("Emergency lead — needs a human", {
      body: `${formatPhone(e.user_phone)} — ${e.reason}`,
      tag: e.id,
    });
  } catch {
    // Notification constructor can throw in some embedded/webview contexts.
  }
}

/**
 * Corner alert for a brand-new, unclaimed escalation (transfer_to_human
 * lead), mounted once in DashboardShell so it reaches every dashboard route
 * regardless of which page a team member is on. Deliberately non-blocking —
 * a fixed card near the toast region, not a full-screen takeover — so it's
 * hard to miss without stopping whatever the team member is doing.
 *
 * Polls via the same useEscalations() hook as the /escalations page
 * (POLL.escalations, 3s) — this app has no push/websocket infra, so "new"
 * is detected by diffing against a set of already-seen lead ids rather than
 * a server-pushed event. Only `recent_leads` rows are alertable (they carry
 * an id to claim); raw queue entries are covered by the /escalations table.
 */
export function EmergencyEscalationPopup() {
  const { data } = useEscalations();
  const claimEscalation = useClaimEscalation();
  const { toast } = useToast();
  const router = useRouter();

  const [queue, setQueue] = useState<Escalation[]>([]);
  const seenIds = useRef<Set<string> | null>(null);
  const snoozedUntil = useRef<Map<string, number>>(new Map());
  const [notifPermission, setNotifPermission] = useState<NotificationPermission | "unsupported">(
    typeof Notification === "undefined" ? "unsupported" : Notification.permission,
  );

  // Diff each poll against what's already been seen/queued. First real
  // response just seeds `seenIds` (silently) so opening the dashboard
  // doesn't fire a burst of alerts for escalations that were already sitting
  // there.
  useEffect(() => {
    if (data === undefined) return;
    const leads = data.recent_leads;
    const unclaimed = leads.filter((l) => !l.claimed_by_account_user_id);

    if (seenIds.current === null) {
      seenIds.current = new Set(unclaimed.map((l) => l.id));
      return;
    }

    const fresh = unclaimed.filter((l) => !seenIds.current!.has(l.id));
    fresh.forEach((l) => seenIds.current!.add(l.id));

    // A queued-but-now-claimed-by-someone-else alert should stop nagging.
    const stillUnclaimedIds = new Set(unclaimed.map((l) => l.id));
    setQueue((prev) => {
      const remaining = prev.filter((e) => e.id && stillUnclaimedIds.has(e.id));
      const claimedAway = prev.filter((e) => e.id && !stillUnclaimedIds.has(e.id) && !fresh.some((l) => l.id === e.id));
      claimedAway.forEach((e) => {
        const lead = leads.find((l) => l.id === e.id);
        toast({
          title: "Claimed",
          description: lead?.claimed_by_name ? `${lead.claimed_by_name} took this one` : undefined,
          variant: "info",
        });
      });

      const next = fresh.map<Escalation>((l) => {
        const meta = l.metadata_ ?? {};
        return {
          source: "lead",
          id: l.id,
          created_at: l.created_at,
          user_id: l.user_id,
          user_phone: l.phone,
          reason: typeof meta.reason === "string" ? meta.reason : "—",
          urgency: typeof meta.urgency === "string" ? meta.urgency : "medium",
          channel: l.channel,
          conversation_id: l.conversation_id,
        };
      });
      if (next.length > 0) playAlertSound();
      next.forEach(notifyBrowser);

      return [...remaining, ...next];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // Skip anything currently snoozed to find what's actually on screen.
  const now = Date.now();
  const active = queue.find((e) => !e.id || (snoozedUntil.current.get(e.id) ?? 0) <= now);

  function requeueLater(id: string) {
    snoozedUntil.current.set(id, Date.now() + SNOOZE_MS);
    // Writing to the ref alone doesn't re-render, so the popup wouldn't
    // actually disappear until some unrelated state change (the next 3s
    // poll) happened to sweep through — force one now so "Not now"/X hide
    // it immediately, then again after the snooze window so it can resurface.
    setQueue((prev) => [...prev]);
    setTimeout(() => setQueue((prev) => [...prev]), SNOOZE_MS + 50);
  }

  function handleSnooze() {
    if (!active?.id) return;
    requeueLater(active.id);
  }

  function handleClaim() {
    if (!active?.id) return;
    const id = active.id;
    claimEscalation.mutate(
      { leadId: id },
      {
        onSuccess: () => {
          setQueue((prev) => prev.filter((e) => e.id !== id));
          toast({ title: "You claimed this lead", variant: "success" });
          if (active.conversation_id) router.push(`/conversations/${active.conversation_id}`);
        },
        onError: (err) => {
          setQueue((prev) => prev.filter((e) => e.id !== id));
          toast({ title: "Couldn't claim", description: err.message, variant: "error" });
        },
      },
    );
  }

  function enableNotifications() {
    if (typeof Notification === "undefined") return;
    Notification.requestPermission().then(setNotifPermission);
  }

  if (!active) return null;

  const ChannelIcon = active.channel === "voice" ? Phone : MessageSquare;

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="pointer-events-none fixed right-4 top-4 z-50 w-[calc(100%-2rem)] max-w-sm"
    >
      <div className="pointer-events-auto relative animate-fade-up overflow-hidden rounded-2xl border border-red-200 bg-white shadow-card-lg dark:border-red-500/30 dark:bg-slate-900">
        <span className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-red-500 to-red-600" aria-hidden />
        <button
          type="button"
          onClick={handleSnooze}
          aria-label="Not now"
          className="absolute right-3 top-3 rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:hover:bg-slate-800 dark:hover:text-slate-300"
        >
          <X size={16} aria-hidden />
        </button>

        <div className="px-4 pb-4 pt-5">
          <div className="flex items-start gap-2.5 pr-6">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-red-100 text-red-600 dark:bg-red-500/15 dark:text-red-300">
              <AlertTriangle size={16} aria-hidden />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-bold leading-snug text-slate-900 dark:text-slate-50">
                A lead needs a human, now
              </p>
              <p className="mt-0.5 font-mono text-xs text-slate-600 dark:text-slate-400">
                {formatPhone(active.user_phone)}
              </p>
            </div>
          </div>

          <div className="mt-2.5 flex flex-wrap items-center gap-1.5 pl-[42px]">
            <UrgencyBadge urgency={active.urgency} className="px-2 py-0.5" />
            <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600 ring-1 ring-inset ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700">
              <ChannelIcon size={10} aria-hidden className="shrink-0" />
              {active.channel === "voice" ? "Call" : "WhatsApp"}
            </span>
            <span className="inline-flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">
              <Clock size={10} aria-hidden />
              {formatRelative(active.created_at)}
            </span>
          </div>

          <p className="mt-2 pl-[42px] text-xs leading-relaxed text-slate-600 dark:text-slate-400">
            {active.reason}
          </p>

          <div className="mt-3 flex items-center gap-2 pl-[42px]">
            <button
              type="button"
              onClick={handleClaim}
              disabled={claimEscalation.isPending}
              className="inline-flex items-center gap-1 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 disabled:opacity-60 dark:bg-red-500 dark:hover:bg-red-400"
            >
              Claim &amp; open
              <ArrowRight size={13} aria-hidden />
            </button>
            <button
              type="button"
              onClick={handleSnooze}
              className="text-xs font-medium text-slate-500 transition-colors hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
            >
              Not now
            </button>
            {queue.length > 1 && (
              <span className="ml-auto text-[11px] text-slate-400 dark:text-slate-500">
                +{queue.length - 1} more
              </span>
            )}
          </div>

          {notifPermission === "default" && (
            <button
              type="button"
              onClick={enableNotifications}
              className="mt-2.5 flex items-center gap-1 pl-[42px] text-[11px] font-medium text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
            >
              <BellRing size={11} aria-hidden />
              Enable desktop alerts
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
