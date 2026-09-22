"use client";

import { useEffect, useRef, useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { z } from "zod";
import { useTheme } from "next-themes";
import { LogIn, AlertCircle, Sun, Moon } from "lucide-react";
import Button from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";
import { login as loginRequest, type OrgChoice } from "@/lib/hooks/useAuthApi";

const tokenSchema = z.object({
  loginToken: z.string().trim().min(1, "Token is required"),
});

export default function LoginPage() {
  const router = useRouter();
  const { login, loginAdmin } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const [loginToken, setLoginToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Set only when this token's account belongs to more than one org — the
  // user picks one before we resubmit login with that org_id. See
  // routers/auth.py's login / LoginOrgChoiceOut.
  const [orgChoices, setOrgChoices] = useState<OrgChoice[] | null>(null);
  const isDark = resolvedTheme === "dark";
  const bgVideo = useRef<HTMLVideoElement>(null);

  // React doesn't reliably emit the `muted` attribute, and browsers only
  // autoplay muted video — set it (and start playback) explicitly once.
  useEffect(() => {
    const video = bgVideo.current;
    if (!video) return;
    video.muted = true;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      // The poster is shown instead (see motion-reduce classes below); don't
      // keep decoding a hidden video.
      video.pause();
      return;
    }
    video.play().catch(() => {
      // Autoplay blocked (e.g. data-saver / low-power mode): the poster stays.
    });
  }, []);

  /** Shared by the initial submit and the org-choice picker's "Continue"
   * click — resolves the same way both times, just with `orgId` set on the
   * second call once the user has picked one. */
  async function signInWithOrg(orgId?: string) {
    const result = await loginRequest(loginToken.trim(), orgId);
    if (result.requires_org_selection) {
      setOrgChoices(result.orgs);
      return;
    }
    login(result);
    router.push("/");
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const parsed = tokenSchema.safeParse({ loginToken });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Please enter your login token.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await signInWithOrg();
    } catch (err) {
      const status = (err as { status?: number } | undefined)?.status;
      if (status === 401) {
        try {
          const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";
          const response = await fetch(`${base}/admin/settings`, {
            headers: { "X-Admin-Token": loginToken.trim() },
          });
          if (!response.ok) {
            throw new Error("admin token rejected");
          }
          await loginAdmin(loginToken.trim());
          router.push("/");
          return;
        } catch {
          setError("Invalid login token.");
          return;
        }
      }
      setError("Couldn't reach the API to sign in. Is the backend running?");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOrgChoice(orgId: string) {
    setSubmitting(true);
    setError(null);
    try {
      await signInWithOrg(orgId);
    } catch {
      setError("Couldn't sign in to that organization. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative isolate flex min-h-screen overflow-hidden bg-slate-950">
      {/* Looping brand video behind the whole page. Muted, no controls, not
          announced to screen readers. Visitors who prefer reduced motion get the
          still poster instead, and the poster also shows while the video loads. */}
      <video
        ref={bgVideo}
        className="absolute inset-0 -z-20 h-full w-full object-cover motion-reduce:hidden"
        src="/login-bg.mp4"
        poster="/login-bg.jpg"
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        aria-hidden="true"
        tabIndex={-1}
      />
      <div
        className="absolute inset-0 -z-20 hidden bg-cover bg-center motion-reduce:block"
        style={{ backgroundImage: "url(/login-bg.jpg)" }}
        aria-hidden="true"
      />
      {/* Dark wash so text and the form stay readable over the animation. */}
      <div
        className="absolute inset-0 -z-10 bg-gradient-to-r from-slate-950/85 via-slate-950/55 to-slate-950/75"
        aria-hidden="true"
      />

      {/* Brand panel */}
      <div className="hidden shrink-0 flex-col justify-between px-14 py-14 lg:flex lg:w-[46%]">
        <div className="flex items-center gap-3">
          <img src="/icon-mark.png" alt="Work Assign Ai" className="h-9 w-9 rounded-[10px] object-contain" />
          <div className="leading-tight">
            <div className="text-[17px] font-extrabold tracking-wide text-white">Work Assign Ai</div>
            <div className="text-[10px] font-semibold tracking-[0.18em] text-slate-300">VIROX</div>
          </div>
        </div>

        <div className="mt-14 max-w-md">
          <h1 className="text-2xl font-bold leading-snug tracking-tight text-white">
            One dashboard for every call and chat your agent handles.
          </h1>
          <p className="mt-3 text-sm leading-relaxed text-slate-300">
            Voice calls and WhatsApp conversations, leads captured, appointments booked — handed to
            a human on your team only when it matters.
          </p>

          <div className="mt-9 flex flex-col gap-3">
            <div className="rounded-xl border border-white/15 bg-white/10 p-4 shadow-card backdrop-blur-md">
              <div className="mb-2.5 flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-bold text-primary-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-primary-500" /> Voice
                </div>
                <div className="font-mono text-[11px] text-slate-400">
                  +1 (415) 555-0138 · 2:14
                </div>
              </div>
              <p className="mb-1 text-[13px] leading-relaxed text-slate-300">
                <span className="font-medium text-white">Agent:</span> I can
                get you booked for Thursday at 2pm — does that work?
              </p>
              <p className="text-[13px] leading-relaxed text-slate-300">
                Customer: Yeah, that&apos;s perfect.
              </p>
            </div>

            <div className="rounded-xl border border-white/15 bg-white/10 p-4 shadow-card backdrop-blur-md">
              <div className="mb-2.5 flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-bold text-emerald-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> WhatsApp
                </div>
                <div className="flex items-center gap-1.5 font-mono text-[11px] text-slate-400">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" /> live
                </div>
              </div>
              <p className="mb-1 text-[13px] leading-relaxed text-slate-300">
                Priya M.: Hi, I saw your ad about the consultation offer.
              </p>
              <p className="text-[13px] leading-relaxed text-slate-300">
                <span className="font-medium text-white">Agent:</span> Sure!
                Let me check availability for you now.
              </p>
            </div>
          </div>

          <p className="mt-6 text-xs leading-relaxed text-slate-400">
            Every conversation, on every channel, in one place — with a human ready to step in the
            moment your agent needs one.
          </p>
        </div>

        <div className="text-xs text-slate-400">© 2026 Virox · Work Assign Ai</div>
      </div>

      {/* Form panel */}
      <div className="relative flex flex-1 items-center justify-center px-6 py-12">
        <button
          type="button"
          onClick={() => setTheme(isDark ? "light" : "dark")}
          aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
          className="absolute right-6 top-6 flex h-9 w-9 items-center justify-center rounded-full border border-white/25 bg-white/10 text-slate-200 shadow-sm backdrop-blur transition-colors hover:bg-white/20 hover:text-white"
        >
          {isDark ? <Moon size={15} /> : <Sun size={15} />}
        </button>

        <div className="w-full max-w-sm rounded-2xl border border-white/15 bg-white/10 p-6 shadow-2xl backdrop-blur-md sm:p-8">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <img src="/icon-mark.png" alt="Work Assign Ai" className="h-9 w-9 rounded-[10px] object-contain" />
            <div className="text-[17px] font-extrabold tracking-wide text-white">Work Assign Ai</div>
          </div>

          {orgChoices ? (
            <>
              <h2 className="text-xl font-bold text-white">Choose an organization</h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-300">
                Your login token is shared by more than one organization — pick the one you want to
                sign in to.
              </p>

              <div className="mt-7 flex flex-col gap-2">
                {orgChoices.map((org) => (
                  <button
                    key={org.org_id}
                    type="button"
                    disabled={submitting}
                    onClick={() => handleOrgChoice(org.org_id)}
                    className="flex items-center justify-between rounded-xl border border-white/15 bg-white/10 px-4 py-3 text-left text-sm shadow-card backdrop-blur-sm transition-colors hover:border-primary-300/60 hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span className="font-medium text-white">{org.org_name}</span>
                    <span className="rounded-full bg-white/10 px-2.5 py-1 text-[11px] font-semibold capitalize text-slate-200">
                      {org.role}
                    </span>
                  </button>
                ))}
              </div>

              {error && (
                <p className="mt-4 flex items-center gap-2 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-200 ring-1 ring-red-400/30">
                  <AlertCircle size={14} />
                  {error}
                </p>
              )}

              <button
                type="button"
                onClick={() => {
                  setOrgChoices(null);
                  setError(null);
                }}
                className="mt-5 text-xs font-medium text-primary-300 hover:text-primary-200"
              >
                ← Use a different token
              </button>
            </>
          ) : (
            <>
              <h2 className="text-xl font-bold text-white">Sign in</h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-300">
                Enter your organization&apos;s login token, or the shared admin token if you manage the
                platform.
              </p>

              <form onSubmit={handleSubmit} className="mt-7 flex flex-col gap-4" noValidate>
                <div>
                  <Label htmlFor="loginToken" className="text-slate-200 dark:text-slate-200">
                    Login token
                  </Label>
                  <div className="relative">
                    <Input
                      id="loginToken"
                      type={showToken ? "text" : "password"}
                      autoComplete="current-password"
                      value={loginToken}
                      onChange={(e) => {
                        setLoginToken(e.target.value);
                        setError(null);
                      }}
                      placeholder="veerox_live_••••••••••••"
                      aria-invalid={error ? true : undefined}
                      className="border-white/20 bg-white/10 pr-14 font-mono text-xs text-white shadow-none backdrop-blur-sm placeholder:text-slate-300 dark:border-white/20 dark:bg-white/10 dark:text-white dark:placeholder:text-slate-300 [&:-webkit-autofill]:shadow-[inset_0_0_0_1000px_rgba(15,23,42,0.85)] [&:-webkit-autofill]:[-webkit-text-fill-color:white]"
                    />
                    <button
                      type="button"
                      onClick={() => setShowToken((s) => !s)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 font-mono text-[11px] text-slate-300 hover:text-white"
                    >
                      {showToken ? "hide" : "show"}
                    </button>
                  </div>
                  <p className="mt-1.5 text-xs text-slate-300">
                    Tokens are never stored in plain text after sign in.
                  </p>
                </div>

                {error && (
                  <p className="flex items-center gap-2 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-200 ring-1 ring-red-400/30">
                    <AlertCircle size={14} />
                    {error}
                  </p>
                )}

                <Button type="submit" variant="primary" className="mt-1 w-full gap-2 py-2.5" loading={submitting}>
                  {!submitting && <LogIn size={15} />} {submitting ? "Signing in…" : "Sign in"}
                </Button>
              </form>

              <div className="mt-5 flex items-center justify-between text-xs">
                <Link
                  href="/forgot-token"
                  className="font-medium text-primary-300 hover:text-primary-200"
                >
                  Forgot your token?
                </Link>
                <span className="text-slate-300">No token? Ask your admin.</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
