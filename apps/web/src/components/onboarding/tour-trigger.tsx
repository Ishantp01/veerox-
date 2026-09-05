"use client";

import { Compass } from "lucide-react";
import { useStartTour } from "./onboarding-tour";

/**
 * "Take the tour" affordance — replays the walkthrough after it's been
 * dismissed. Must be rendered inside `<OnboardingTour>` (i.e. anywhere in the
 * dashboard route group).
 */
export function TourTrigger({ className }: { className?: string }) {
  const startTour = useStartTour();

  return (
    <button
      type="button"
      onClick={startTour}
      className={
        className ??
        "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
      }
    >
      <Compass size={14} aria-hidden />
      Take the tour
    </button>
  );
}
