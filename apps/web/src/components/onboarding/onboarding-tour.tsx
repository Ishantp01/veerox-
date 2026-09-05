"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Onborda, OnbordaProvider, useOnborda } from "onborda";
import type { OnbordaProps } from "onborda";
import { useAuth } from "@/lib/auth-context";
import {
  ALL_STEPS,
  buildPageGuideTour,
  buildTour,
  TOUR_NEW_ACCOUNT,
  TOUR_PAGE_GUIDE,
} from "@/lib/onboarding/tours";
import { hasSeenTour } from "@/lib/onboarding/tour-state";
import { TourCard } from "./tour-card";

interface TourApi {
  /** Full one-step-per-module walkthrough. */
  startTour: () => void;
  /** "How to use this page" tour for the route passed in (defaults to current). */
  startPageGuide: (pathname?: string) => void;
  /** Whether the current route has a page guide available. */
  hasPageGuide: boolean;
}

const TourContext = createContext<TourApi>({
  startTour: () => {},
  startPageGuide: () => {},
  hasPageGuide: false,
});

export function useTour(): TourApi {
  return useContext(TourContext);
}

/** Back-compat: existing call sites import `useStartTour`. */
export function useStartTour(): () => void {
  return useContext(TourContext).startTour;
}

const EMPTY_PAGE_GUIDE: OnbordaProps["steps"][number] = { tour: TOUR_PAGE_GUIDE, steps: [] };
const INITIAL_STEPS: OnbordaProps["steps"] = [
  { tour: TOUR_NEW_ACCOUNT, steps: ALL_STEPS },
  EMPTY_PAGE_GUIDE,
];

/**
 * Inline overrides on Onborda's own markup so a stale CSS build can't break the
 * card: its wrapper is `max-w-[100%]` of the highlighted element (squashes the
 * card, clips "Next"), and its arrow SVG has no intrinsic size.
 */
const ONBORDA_CSS = `
[data-name="onborda-card"]{max-width:min(340px,calc(100vw - 2rem))!important;width:max-content!important;}
[data-name="onborda-arrow"]{width:1.5rem!important;height:1.5rem!important;}
`;

/**
 * Onborda runs a smooth `scrollIntoView` on each step's target, then measures
 * it synchronously — before the scroll settles — so the highlight can land off
 * target. It re-measures (without re-scrolling) on `window` resize; nudging it
 * with synthetic resize events after the scroll finishes fixes the position.
 */
function PointerResync() {
  const { currentStep, isOnbordaVisible } = useOnborda();
  useEffect(() => {
    if (!isOnbordaVisible) return;
    const timers = [80, 250, 500, 900].map((d) =>
      window.setTimeout(() => window.dispatchEvent(new Event("resize")), d),
    );
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, [currentStep, isOnbordaVisible]);
  return null;
}

/**
 * Skips a step whose anchor isn't in the DOM (a link this account doesn't get,
 * a page without that toolbar, a stale bundle). Without it Onborda leaves the
 * highlight stuck on the previous step.
 */
function SkipMissingSteps({ steps }: { steps: OnbordaProps["steps"] }) {
  const { currentStep, currentTour, isOnbordaVisible, setCurrentStep, closeOnborda } = useOnborda();
  useEffect(() => {
    if (!isOnbordaVisible || !currentTour) return;
    const tourSteps = steps.find((t) => t.tour === currentTour)?.steps ?? [];
    const selector = tourSteps[currentStep]?.selector;
    if (!selector) return;
    const id = window.setTimeout(() => {
      if (typeof document === "undefined" || document.querySelector(selector)) return;
      if (currentStep < tourSteps.length - 1) setCurrentStep(currentStep + 1);
      else closeOnborda();
    }, 450);
    return () => window.clearTimeout(id);
  }, [currentStep, currentTour, isOnbordaVisible, steps, setCurrentStep, closeOnborda]);
  return null;
}

/** First-time triggers: the full tour once per new account, then each page's
 *  guide once per route. Both skip platform/staff accounts and small screens. */
function AutoStart({ startTour, startPageGuide }: TourApi) {
  const { status, user } = useAuth();
  const pathname = usePathname();
  const ranFullTour = useRef(false);

  const eligible =
    status === "authenticated" &&
    !!user &&
    !user.is_superuser &&
    !user.is_platform_org &&
    typeof window !== "undefined" &&
    window.innerWidth >= 1024;

  // Full walkthrough — once, on first authenticated load.
  useEffect(() => {
    if (ranFullTour.current || !eligible || !user) return;
    if (hasSeenTour(user.account_user_id)) return;
    ranFullTour.current = true;
    const t = window.setTimeout(startTour, 900);
    return () => window.clearTimeout(t);
  }, [eligible, user, startTour]);

  // Page guide — once per route (only while the new-account tour is still
  // pending, so established users aren't interrupted on every page).
  useEffect(() => {
    if (!eligible || !user) return;
    // The dashboard is covered by the full walkthrough — don't double up.
    if (pathname === "/") return;
    if (hasSeenTour(user.account_user_id)) return;
    const key = `veerox_pageguide_seen_${pathname}`;
    try {
      if (localStorage.getItem(key)) return;
      localStorage.setItem(key, "1");
    } catch {
      return;
    }
    const t = window.setTimeout(() => startPageGuide(pathname), 1100);
    return () => window.clearTimeout(t);
  }, [pathname, eligible, user, startPageGuide]);

  return null;
}

function OnbordaInner({ children }: { children: React.ReactNode }) {
  const { startOnborda } = useOnborda();
  const pathname = usePathname();
  const [steps, setSteps] = useState<OnbordaProps["steps"]>(INITIAL_STEPS);

  const hasPageGuide = buildPageGuideTour(pathname).steps.length > 0;

  const runStartTour = useCallback(
    (attempt: number) => {
      const full = buildTour();
      if (full.steps.length < 2 && attempt < 6) {
        window.setTimeout(() => runStartTour(attempt + 1), 250);
        return;
      }
      const mainTour = full.steps.length >= 2 ? full : { tour: TOUR_NEW_ACCOUNT, steps: ALL_STEPS };
      setSteps([mainTour, buildPageGuideTour(pathname)]);
      startOnborda(TOUR_NEW_ACCOUNT);
    },
    [startOnborda, pathname],
  );

  const runStartPageGuide = useCallback(
    (path: string, attempt: number) => {
      const guide = buildPageGuideTour(path);
      if (guide.steps.length === 0) return;
      const resolvable =
        typeof document === "undefined"
          ? guide.steps
          : guide.steps.filter((s) => document.querySelector(s.selector));

      if (resolvable.length === 0) {
        if (attempt < 6) {
          window.setTimeout(() => runStartPageGuide(path, attempt + 1), 250);
          return;
        }
        // Page-specific anchors never showed up (e.g. a stale bundle after a
        // hot reload) — still run the guide, pinned to the always-present page
        // container so the user isn't left with a dead button.
        const rooted = guide.steps.map((s) => ({
          ...s,
          selector: '[data-tour="page-root"]',
          side: undefined,
        }));
        setSteps([buildTour(), { tour: TOUR_PAGE_GUIDE, steps: rooted }]);
        startOnborda(TOUR_PAGE_GUIDE);
        return;
      }

      setSteps([buildTour(), { tour: TOUR_PAGE_GUIDE, steps: resolvable }]);
      startOnborda(TOUR_PAGE_GUIDE);
    },
    [startOnborda],
  );

  const startTour = useCallback(() => runStartTour(0), [runStartTour]);
  const startPageGuide = useCallback(
    (path?: string) => runStartPageGuide(path ?? pathname, 0),
    [runStartPageGuide, pathname],
  );

  const api = useMemo<TourApi>(
    () => ({ startTour, startPageGuide, hasPageGuide }),
    [startTour, startPageGuide, hasPageGuide],
  );

  return (
    <TourContext.Provider value={api}>
      <style dangerouslySetInnerHTML={{ __html: ONBORDA_CSS }} />
      <Onborda
        steps={steps}
        cardComponent={TourCard}
        shadowRgb="10, 13, 20"
        shadowOpacity="0.6"
        cardTransition={{ type: "spring", damping: 26, stiffness: 260 }}
      >
        <AutoStart {...api} />
        <PointerResync />
        <SkipMissingSteps steps={steps} />
        {children}
      </Onborda>
    </TourContext.Provider>
  );
}

export function OnboardingTour({ children }: { children: React.ReactNode }) {
  return (
    <OnbordaProvider>
      <OnbordaInner>{children}</OnbordaInner>
    </OnbordaProvider>
  );
}
