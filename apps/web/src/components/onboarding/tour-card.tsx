"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CardComponentProps } from "onborda";
import { useOnborda } from "onborda";
import { X } from "lucide-react";
import { Button } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { markTourSeen } from "@/lib/onboarding/tour-state";

/**
 * Custom Onborda card, styled to match the app's surfaces. Onborda positions
 * this next to the highlighted element with no viewport-collision handling, so
 * a step whose target sits near a screen edge can push the card (and its
 * Back / Next buttons) off-screen. Two guards against that:
 *
 *  1. The card is a flex column capped at the viewport height — the header and
 *     the Back/Next footer never scroll, only the body does.
 *  2. After each step we measure the card and, if it still overflows the
 *     viewport, nudge it back in with a translate (and drop the now-misaligned
 *     arrow).
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
  const cardRef = useRef<HTMLDivElement>(null);
  const [nudge, setNudge] = useState(0);

  const isFirst = currentStep === 0;
  const isLast = currentStep === totalSteps - 1;

  function endTour() {
    if (user) markTourSeen(user.account_user_id);
    closeOnborda();
  }

  const reposition = useCallback(() => {
    const el = cardRef.current;
    if (!el) return;
    // Measure without our own offset so the maths is stable across calls.
    const prev = el.style.transform;
    el.style.transform = "";
    const r = el.getBoundingClientRect();
    el.style.transform = prev;

    // Keep clear of the sticky 64px topbar at the top, a small gap at the bottom.
    const topSafe = 76;
    const bottomSafe = 16;
    let dy = 0;
    if (r.bottom > window.innerHeight - bottomSafe) dy = window.innerHeight - bottomSafe - r.bottom;
    if (r.top + dy < topSafe) dy = topSafe - r.top;
    setNudge(Math.round(dy));
  }, []);

  useLayoutEffect(() => {
    setNudge(0);
    // let Onborda place the card first, then correct it
    const raf = requestAnimationFrame(reposition);
    return () => cancelAnimationFrame(raf);
  }, [currentStep, reposition]);

  useEffect(() => {
    window.addEventListener("resize", reposition);
    return () => window.removeEventListener("resize", reposition);
  }, [reposition]);

  return (
    <div
      ref={cardRef}
      style={{ transform: nudge ? `translateY(${nudge}px)` : undefined }}
      className="relative flex max-h-[calc(100vh-1.5rem)] w-[320px] max-w-[calc(100vw-2rem)] flex-col rounded-2xl border border-slate-200 bg-white text-slate-700 shadow-card-lg dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
    >
      {/* Onborda pointer triangle — hidden once we've had to nudge the card,
          because it would point at nothing. */}
      {!nudge && <span className="text-white dark:text-slate-900">{arrow}</span>}

      <div className="flex shrink-0 items-start justify-between gap-3 p-5 pb-3">
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

      <div className="min-h-0 flex-1 overflow-y-auto px-5 text-[13px] leading-relaxed text-slate-500 dark:text-slate-400">
        {step.content}
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 p-5 pt-4">
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
