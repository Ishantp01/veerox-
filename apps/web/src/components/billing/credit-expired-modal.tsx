"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, RefreshCw, X } from "lucide-react";
import { Button, useToast } from "@/components/ui";
import { CREDIT_LIMIT_EVENT } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  CREDIT_METRICS,
  isMetricExhausted,
  useBillingStatus,
  useBillingUsage,
  useIsOutOfCredit,
} from "@/lib/hooks/useBilling";

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';

/**
 * How long a dismissal buys before the modal comes back. The block is real —
 * outbound calls and messages are refused server-side (deps.py's
 * enforce_plan_limit) — so closing this is a snooze, never a permanent
 * opt-out. It keeps returning on this cadence until the org recharges.
 */
const SNOOZE_MS = 5 * 60 * 1000;

/**
 * Recharge prompt for an org that has run out of plan credit, mounted once in
 * DashboardShell so it reaches every dashboard route. Triggered by
 * useIsOutOfCredit — any metered credit spent, or billing lapsed.
 *
 * A centred modal over a full-bleed backdrop. The backdrop is plain
 * `inset-0`, unlike the shared DialogContent's `lg:left-64`, so the sidebar
 * is covered too and the nav can't be used to click past it; focus is
 * trapped for the same reason. It is not built on <Dialog> because of that
 * inset and because closing needs to snooze rather than simply dismiss.
 *
 * Closing it — X, Escape, backdrop, or "Remind me later" — snoozes for
 * SNOOZE_MS and then it reappears on its own, without needing a navigation
 * or another API call to trigger it.
 *
 * The /billing routes are exempt — the modal must never cover the checkout
 * buttons that clear it.
 *
 * Auto-clearing on recharge is free: both billing queries poll on
 * POLL.billing and refetch on focus, and verify-payment flips the org to
 * active with a fresh plan_started_at (routers/billing.py), which is what
 * get_credit_usage counts from. So the moment payment lands, the numbers
 * below reset and this closes itself — no reload. A 402 from any API call
 * additionally kicks the queries immediately (see CREDIT_LIMIT_EVENT in
 * lib/api.ts) rather than waiting on the next tick.
 */
export function CreditExpiredModal() {
  const { user, logout } = useAuth();
  const billing = useBillingStatus();
  const usage = useBillingUsage();
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [snoozedUntil, setSnoozedUntil] = useState(0);
  // Bumped by the snooze timer / a 402 so the `snoozedUntil > now` check
  // below re-evaluates — state, not a bare Date.now() read, because the
  // snooze expiring has to trigger a render on its own.
  const [now, setNow] = useState(() => Date.now());

  // A plain member can't reach /billing at all (the dashboard layout
  // redirects MEMBER_RESTRICTED_PREFIXES away), so pointing them at
  // checkout would just bounce them to "/". They still get the prompt —
  // they need to know why their calls/messages stopped — but with a "tell
  // your admin" message instead of a dead CTA.
  const canRecharge = user?.role !== "member";
  // Never cover the pages that exist to fix the problem.
  const onBillingRoute = pathname === "/billing" || pathname.startsWith("/billing/");

  const blocked = useIsOutOfCredit();
  const open = blocked && !onBillingRoute && snoozedUntil <= now;
  // Only changes the wording — both cases block identically. A lapsed
  // billing_status means a payment failed or an admin suspended the org,
  // which is a different thing to explain than simply spending the credits.
  const lapsed =
    billing.data !== undefined &&
    ["past_due", "canceled", "incomplete"].includes(billing.data.billing_status);

  function snooze() {
    setSnoozedUntil(Date.now() + SNOOZE_MS);
  }

  // Wake up when the snooze runs out so the modal reappears by itself.
  useEffect(() => {
    if (snoozedUntil <= Date.now()) return;
    const timer = setTimeout(() => setNow(Date.now()), snoozedUntil - Date.now());
    return () => clearTimeout(timer);
  }, [snoozedUntil]);

  useEffect(() => {
    function onCreditLimit() {
      // A request was just refused for credit — re-arm and pull fresh
      // status/usage so the prompt reflects the block immediately.
      setSnoozedUntil(0);
      setNow(Date.now());
      queryClient.invalidateQueries({ queryKey: ["billing"] });
    }
    window.addEventListener(CREDIT_LIMIT_EVENT, onCreditLimit);
    return () => window.removeEventListener(CREDIT_LIMIT_EVENT, onCreditLimit);
  }, [queryClient]);

  // Recharge landed: confirm it out loud (the modal just vanishing is easy
  // to miss) and clear any snooze so a later exhaustion pops a fresh prompt
  // rather than an already-snoozed one.
  const wasBlocked = useRef(false);
  useEffect(() => {
    if (wasBlocked.current && !blocked) {
      setSnoozedUntil(0);
      toast({ title: "Credits updated — you're good to go", variant: "success" });
    }
    wasBlocked.current = blocked;
  }, [blocked, toast]);

  // Lock background scroll and keep focus inside the panel while it's up.
  const panelRef = useRef<HTMLDivElement>(null);
  // Escape closes via snooze, so the handler needs the latest closure
  // without re-running the effect (and re-stealing focus) on every render.
  const snoozeRef = useRef(snooze);
  snoozeRef.current = snooze;

  useEffect(() => {
    if (!open) return;

    panelRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        snoozeRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null
      );
      if (items.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === panel)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = prevOverflow;
    };
  }, [open]);

  if (!open) return null;

  const usageData = usage.data;
  const planName = billing.data?.plan?.name;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center overflow-y-auto p-4"
      onMouseDown={(e) => {
        // Close only when the backdrop itself is clicked, not the panel.
        if (e.target === e.currentTarget) snooze();
      }}
    >
      <div className="absolute inset-0 bg-slate-900/70 backdrop-blur-sm dark:bg-black/80" aria-hidden />

      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="credit-gate-title"
        tabIndex={-1}
        className="relative z-10 my-auto w-full max-w-lg animate-fade-up overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card-lg focus-visible:outline-none dark:border-slate-800 dark:bg-slate-900"
      >
        <span className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-red-500 to-red-600" aria-hidden />
        <button
          type="button"
          onClick={snooze}
          aria-label="Remind me later"
          className="absolute right-4 top-4 rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:hover:bg-slate-800 dark:hover:text-slate-300"
        >
          <X size={18} aria-hidden />
        </button>

        <div className="px-6 pb-6 pt-7 sm:px-7">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-red-100 text-red-600 dark:bg-red-500/15 dark:text-red-300">
            <AlertTriangle size={24} aria-hidden />
          </span>

          <h2
            id="credit-gate-title"
            className="mt-4 pr-8 text-2xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50"
          >
            {lapsed ? "Your billing is on hold" : "You're out of credits"}
          </h2>

          <p className="mt-2.5 text-sm leading-relaxed text-slate-600 dark:text-slate-400">
            {lapsed
              ? "Your billing is on hold, so calls and messages are paused."
              : `You've used up ${planName ? `your ${planName} plan's` : "your plan's"} credits, so calls and messages are paused.`}{" "}
            {canRecharge
              ? "Renew to switch everything back on instantly."
              : "Ask your organisation's admin to renew — everything switches back on the moment they do."}
          </p>

          {usageData && (
            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              {CREDIT_METRICS.map(({ key, label, unit }) => {
                const metric = usageData[key];
                const isOut = isMetricExhausted(metric);
                const remaining =
                  metric.limit === null ? null : Math.max(0, metric.limit - metric.used);
                return (
                  <div
                    key={key}
                    className={`rounded-xl border p-3.5 ${
                      isOut
                        ? "border-red-200 bg-red-50 dark:border-red-500/25 dark:bg-red-500/[0.07]"
                        : "border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60"
                    }`}
                  >
                    <p className="text-[11px] font-bold uppercase tracking-widest text-slate-500 dark:text-slate-400">
                      {label}
                    </p>
                    <p
                      className={`mt-1 text-xl font-extrabold tracking-tight ${
                        isOut
                          ? "text-red-600 dark:text-red-400"
                          : "text-slate-900 dark:text-slate-100"
                      }`}
                    >
                      {remaining === null
                        ? "Unlimited"
                        : `${Math.floor(remaining).toLocaleString()}${unit ? ` ${unit}` : ""} left`}
                    </p>
                    <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                      {Math.round(metric.used).toLocaleString()}
                      {metric.limit !== null ? ` of ${metric.limit.toLocaleString()}` : ""} used
                    </p>
                  </div>
                );
              })}
            </div>
          )}

          {canRecharge && (
            <Button
              variant="danger"
              size="lg"
              className="mt-6 w-full font-semibold"
              onClick={() => router.push("/billing/upgrade")}
            >
              Renew now
              <ArrowRight size={17} aria-hidden />
            </Button>
          )}

          <p className="mt-4 flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <RefreshCw
              size={12}
              aria-hidden
              className={usage.isFetching || billing.isFetching ? "animate-spin" : undefined}
            />
            Credits update automatically — this closes itself the moment your renewal goes through.
          </p>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-6 py-3.5 sm:px-7 dark:border-slate-800">
          <button
            type="button"
            onClick={snooze}
            className="text-sm font-medium text-slate-500 transition-colors hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
          >
            Remind me later
          </button>
          {/* Sign out is the one non-payment exit, kept quiet so it never
              competes with recharging. Without it a member — who can't open
              /billing at all — would have no way off this screen short of
              clearing localStorage. */}
          <button
            type="button"
            onClick={() => logout()}
            className="text-sm font-medium text-slate-400 underline underline-offset-4 transition-colors hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
