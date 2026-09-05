"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { Onborda, OnbordaProvider, useOnborda } from "onborda";
import type { OnbordaProps } from "onborda";
import { useAuth } from "@/lib/auth-context";
import { ALL_STEPS, buildTour, TOUR_NEW_ACCOUNT } from "@/lib/onboarding/tours";
import { hasSeenTour } from "@/lib/onboarding/tour-state";
import { TourCard } from "./tour-card";

/** Starts the walkthrough for the current account. Available anywhere inside
 *  the dashboard route group via `useStartTour()`. */
const StartTourContext = createContext<() => void>(() => {});

export function useStartTour(): () => void {
  return useContext(StartTourContext);
}

const FALLBACK_STEPS: OnbordaProps["steps"] = [{ tour: TOUR_NEW_ACCOUNT, steps: ALL_STEPS }];

/**
 * Overrides on Onborda's own markup, shipped inline so they can't be missed by
 * a stale CSS build:
 *  - its card wrapper is `max-w-[100%]`, where 100% resolves to the width of
 *    the highlighted element — a narrow sidebar link squashes the card and
 *    clips its buttons ("Next" disappears off the right edge);
 *  - its pointer-arrow SVG has no intrinsic size and balloons to 300×150 when
 *    Tailwind hasn't scanned the onborda bundle.
 */
const ONBORDA_CSS = `
[data-name="onborda-card"]{max-width:min(340px,calc(100vw - 2rem))!important;width:max-content!important;}
[data-name="onborda-arrow"]{width:1.5rem!important;height:1.5rem!important;}
`;

/**
 * Onborda always runs a *smooth* `scrollIntoView` on each step's target, then
 * measures its position synchronously — before the scroll settles. When the
 * target sits in a scrollable container (the sidebar overflows once the tour
 * walks through every module) the highlight lands a row or two off, or the
 * card renders off-screen.
 *
 * Onborda re-measures — without re-scrolling — on `window` resize. Nudging it
 * with synthetic resize events after the scroll has had time to finish snaps
 * the highlight (and card) onto the real target.
 */
function PointerResync() {
  const { currentStep, isOnbordaVisible } = useOnborda();

  useEffect(() => {
    if (!isOnbordaVisible) return;
    const timers = [80, 250, 500, 900].map((delay) =>
      window.setTimeout(() => window.dispatchEvent(new Event("resize")), delay),
    );
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, [currentStep, isOnbordaVisible]);

  return null;
}

/**
 * Safety net for a step whose anchor isn't in the DOM at runtime — e.g. a
 * sidebar link this account doesn't get, or a stale bundle that hasn't picked
 * up the anchor ids yet. Without this Onborda leaves the highlight stuck on the
 * previous step's element while showing the new step's copy. Here we just skip
 * forward to the next resolvable step (or close if there are none left).
 */
function SkipMissingSteps({ steps }: { steps: OnbordaProps["steps"] }) {
  const { currentStep, currentTour, isOnbordaVisible, setCurrentStep, closeOnborda } = useOnborda();

  useEffect(() => {
    if (!isOnbordaVisible || !currentTour) return;
    const tourSteps = steps.find((t) => t.tour === currentTour)?.steps ?? [];
    const selector = tourSteps[currentStep]?.selector;
    if (!selector) return;

    // Give the DOM a beat to settle (route change, sidebar render) first.
    const id = window.setTimeout(() => {
      if (typeof document === "undefined" || document.querySelector(selector)) return;
      if (currentStep < tourSteps.length - 1) setCurrentStep(currentStep + 1);
      else closeOnborda();
    }, 450);
    return () => window.clearTimeout(id);
  }, [currentStep, currentTour, isOnbordaVisible, steps, setCurrentStep, closeOnborda]);

  return null;
}

/**
 * Fires the walkthrough once, the first time a real customer account reaches
 * the dashboard. Platform/staff accounts are skipped (they're not onboarding),
 * and so is anything below the `lg` breakpoint — most tour anchors live in the
 * desktop sidebar, which is off-canvas on mobile.
 */
function AutoStartTour({ start }: { start: () => void }) {
  const { status, user } = useAuth();
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    if (status !== "authenticated" || !user) return;
    if (user.is_superuser || user.is_platform_org) return;
    if (typeof window === "undefined" || window.innerWidth < 1024) return;
    if (hasSeenTour(user.account_user_id)) return;

    started.current = true;
    // Let the shell + sidebar finish rendering (nav links are gated by plan
    // and role) so `buildTour()` sees the real module list.
    const t = window.setTimeout(start, 900);
    return () => window.clearTimeout(t);
  }, [status, user, start]);

  return null;
}

function OnbordaInner({ children }: { children: React.ReactNode }) {
  const { startOnborda } = useOnborda();
  const [steps, setSteps] = useState<OnbordaProps["steps"]>(FALLBACK_STEPS);

  const start = useCallback(
    (attempt = 0) => {
      const built = buildTour();
      if (built[0].steps.length >= 2) {
        setSteps(built);
        // React batches this with `setSteps`, so Onborda re-renders once with
        // the fresh `steps` prop already in place before it reads the tour.
        startOnborda(TOUR_NEW_ACCOUNT);
        return;
      }
      // Anchors not mounted yet (first paint, or a just-hot-reloaded bundle) —
      // retry briefly before giving up to the unfiltered list. `SkipMissingSteps`
      // then quietly drops any step whose anchor still isn't there.
      if (attempt < 6) {
        window.setTimeout(() => start(attempt + 1), 250);
        return;
      }
      setSteps(FALLBACK_STEPS);
      startOnborda(TOUR_NEW_ACCOUNT);
    },
    [startOnborda],
  );

  const startTour = useCallback(() => start(0), [start]);

  return (
    <StartTourContext.Provider value={startTour}>
      <style dangerouslySetInnerHTML={{ __html: ONBORDA_CSS }} />
      <Onborda
        steps={steps}
        cardComponent={TourCard}
        shadowRgb="10, 13, 20"
        shadowOpacity="0.6"
        cardTransition={{ type: "spring", damping: 26, stiffness: 260 }}
      >
        <AutoStartTour start={startTour} />
        <PointerResync />
        <SkipMissingSteps steps={steps} />
        {children}
      </Onborda>
    </StartTourContext.Provider>
  );
}

export function OnboardingTour({ children }: { children: React.ReactNode }) {
  return (
    <OnbordaProvider>
      <OnbordaInner>{children}</OnbordaInner>
    </OnbordaProvider>
  );
}
