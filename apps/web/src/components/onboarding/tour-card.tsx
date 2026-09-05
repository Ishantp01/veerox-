"use client";

import type { CardComponentProps } from "onborda";
import { useOnborda } from "onborda";
import { X } from "lucide-react";
import { Button } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { markTourSeen } from "@/lib/onboarding/tour-state";

/**
 * Custom Onborda card, styled to match the app's surfaces (rounded-2xl,
 * hairline border, `shadow-card-lg`, dark-mode aware). Onborda positions this
 * next to the highlighted element and hands us `arrow` — the pointer triangle
 * — plus the step navigation callbacks.
 */
export function TourCard({
  step,
  currentStep,
  totalSteps,
  nextStep,
  prevStep,
  arrow,
}: CardComponentProps) {
  const { closeOnborda } = useOnborda();
  const { user } = useAuth();

  const isFirst = currentStep === 0;
  const isLast = currentStep === totalSteps - 1;

  function endTour() {
    if (user) markTourSeen(user.account_user_id);
    closeOnborda();
  }

  return (
    <div className="relative w-[320px] max-w-[calc(100vw-2rem)] rounded-2xl border border-slate-200 bg-white p-5 text-slate-700 shadow-card-lg dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200">
      {/* Onborda pointer triangle — inherits `currentColor`, so tint it to the card bg */}
      <span className="text-white dark:text-slate-900">{arrow}</span>

      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          {step.icon && (
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-glow">
              {step.icon}
            </span>
          )}
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">{step.title}</h2>
        </div>
        <button
          type="button"
          onClick={endTour}
          aria-label="Skip tour"
          className="-mr-1 -mt-1 shrink-0 rounded-lg p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
        >
          <X size={15} aria-hidden />
        </button>
      </div>

      <div className="mt-3 max-h-[50vh] overflow-y-auto text-[13px] leading-relaxed text-slate-500 dark:text-slate-400">
        {step.content}
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[11px] font-medium tabular-nums text-slate-400 dark:text-slate-500">
          <span>
            {currentStep + 1} / {totalSteps}
          </span>
          <span className="block h-1 w-14 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700" aria-hidden>
            <span
              className="block h-full rounded-full bg-primary-500 transition-all duration-300"
              style={{ width: `${((currentStep + 1) / totalSteps) * 100}%` }}
            />
          </span>
        </div>

        <div className="flex items-center gap-2">
          {!isFirst && (
            <Button variant="ghost" size="sm" onClick={prevStep}>
              Back
            </Button>
          )}
          <Button size="sm" onClick={isLast ? endTour : nextStep}>
            {isLast ? "Get started" : "Next"}
          </Button>
        </div>
      </div>
    </div>
  );
}
