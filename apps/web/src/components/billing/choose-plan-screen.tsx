"use client";

import { useEffect, useState } from "react";
import { Check, Lock, Moon, ShieldCheck, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { Button, Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useBuyPlan } from "@/lib/hooks/useBuyPlan";
import { formatPrice, LIMIT_FIELDS, usePaymentPlans } from "@/lib/hooks/usePayments";

/**
 * Full-screen plan picker shown right after login when the org has no plan yet
 * (see PlanGate). Replaces the whole dashboard (no sidebar) until a payment
 * succeeds, then PlanGate lets the dashboard through. Supports light and dark
 * mode with a toggle, like the login page.
 */
export function ChoosePlanScreen() {
  const { user, logout } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  // next-themes only knows the theme on the client; render the icon after mount
  // so server and client HTML match (avoids a hydration error).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const isDark = resolvedTheme === "dark";
  const plans = usePaymentPlans();
  const { buy, busyPlan, verifying } = useBuyPlan();
  const canBuy = user?.role === "admin" || !!user?.is_superuser;

  return (
    <div className="relative min-h-screen bg-slate-50 px-4 py-12 dark:bg-canvas-950 dark:bg-mesh-dark">
      <button
        type="button"
        onClick={() => setTheme(isDark ? "light" : "dark")}
        aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
        title={isDark ? "Switch to light theme" : "Switch to dark theme"}
        className="absolute right-6 top-6 flex h-9 w-9 items-center justify-center rounded-full border border-slate-300 bg-white text-slate-700 shadow-sm transition-colors hover:bg-slate-100 dark:border-white/25 dark:bg-white/10 dark:text-slate-200 dark:hover:bg-white/20"
      >
        {mounted ? isDark ? <Moon size={15} /> : <Sun size={15} /> : <span className="h-[15px] w-[15px]" />}
      </button>

      {verifying && (
        <div
          role="status"
          className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-slate-50/95 backdrop-blur-sm dark:bg-canvas-950/95"
        >
          <span className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400">
            <Check size={28} aria-hidden />
          </span>
          <p className="text-lg font-semibold text-slate-900 dark:text-slate-50">Payment successful</p>
          <p className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
            <Spinner size={16} label="Activating" /> Activating your plan…
          </p>
        </div>
      )}

      <div className="mx-auto max-w-5xl">
        <div className="text-center">
          <img src="/icon-mark.png" alt="Work Assign Ai" className="mx-auto h-16 w-16 object-contain" />
          <h1 className="mt-5 text-3xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50">
            Choose your plan
          </h1>
          <p className="mx-auto mt-2 max-w-xl text-sm text-slate-500 dark:text-slate-400">
            {canBuy
              ? "Pick a plan to start using your workspace. You can renew or change it any time."
              : "Your organization needs an active plan. Ask your organization admin to choose one."}
          </p>
        </div>

        <div className="mt-10">
          {plans.isLoading ? (
            <div className="flex justify-center py-16">
              <Spinner size={28} label="Loading plans" className="text-slate-500 dark:text-white" />
            </div>
          ) : plans.isError ? (
            <p className="text-center text-sm text-red-600 dark:text-red-300">Could not load plans. Please refresh.</p>
          ) : plans.data?.length === 0 ? (
            <p className="text-center text-sm text-slate-500 dark:text-slate-400">
              No plans are available yet. Please contact support.
            </p>
          ) : (
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {plans.data?.map((plan) => (
                <div
                  key={plan.id}
                  className="flex flex-col rounded-2xl border border-slate-200 bg-white p-6 shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-lg"
                >
                  <h2 className="text-base font-semibold text-slate-900 dark:text-slate-50">{plan.name}</h2>
                  <p className="mt-3 flex items-baseline gap-1.5">
                    <span className="text-4xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50">
                      {formatPrice(plan.price_paise, plan.currency)}
                    </span>
                    <span className="text-sm text-slate-500 dark:text-slate-400">/ {plan.duration_days} days</span>
                  </p>
                  {plan.description && (
                    <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{plan.description}</p>
                  )}
                  <ul className="mt-5 flex-1 space-y-2.5 text-sm text-slate-600 dark:text-slate-300">
                    {LIMIT_FIELDS.map((f) => (
                      <li key={f.key} className="flex gap-2">
                        <Check size={16} className="mt-0.5 shrink-0 text-emerald-500" aria-hidden />
                        <span>
                          {plan[f.key] === null ? "Unlimited" : plan[f.key]!.toLocaleString("en-IN")}{" "}
                          {f.label.toLowerCase()}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <Button
                    size="lg"
                    className="mt-6 w-full"
                    disabled={!canBuy || busyPlan !== null}
                    onClick={() => buy(plan.id)}
                  >
                    {busyPlan === plan.id ? (verifying ? "Activating…" : "Opening checkout…") : "Select plan"}
                  </Button>
                  {!canBuy && (
                    <p className="mt-2 flex items-center justify-center gap-1.5 text-xs text-slate-500">
                      <Lock size={12} aria-hidden /> Only an org admin can purchase
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
          <ShieldCheck size={14} aria-hidden /> Payments are processed securely by Razorpay.
        </p>
        <div className="mt-6 text-center">
          <button
            type="button"
            onClick={() => logout()}
            className="text-sm font-medium text-slate-500 underline underline-offset-4 transition-colors hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
