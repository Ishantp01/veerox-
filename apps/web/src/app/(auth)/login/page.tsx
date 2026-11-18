"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { z } from "zod";
import { LogIn, AlertCircle, Bot, AudioLines, ChevronRight, Phone, Lock } from "lucide-react";
import { WhatsappIcon } from "@/components/ui/whatsapp-icon";
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
  const [loginToken, setLoginToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Set only when this token's account belongs to more than one org — the
  // user picks one before we resubmit login with that org_id. See
  // routers/auth.py's login / LoginOrgChoiceOut.
  const [orgChoices, setOrgChoices] = useState<OrgChoice[] | null>(null);

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
    <div className="relative isolate flex min-h-screen overflow-hidden font-sans antialiased bg-gradient-to-br from-sky-50 via-blue-50 to-white">
      {/* Static layered waves down the right side (no motion). */}
      <svg
        aria-hidden
        viewBox="0 0 800 900"
        preserveAspectRatio="xMaxYMid slice"
        className="pointer-events-none absolute inset-y-0 right-0 -z-10 h-full w-[70%]"
      >
        <defs>
          <linearGradient id="wv1" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#bfdbfe" stopOpacity="0.55" />
            <stop offset="1" stopColor="#93c5fd" stopOpacity="0.35" />
          </linearGradient>
          <linearGradient id="wv2" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#60a5fa" stopOpacity="0.55" />
            <stop offset="1" stopColor="#2563eb" stopOpacity="0.75" />
          </linearGradient>
          <linearGradient id="wv3" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#2563eb" stopOpacity="0.8" />
            <stop offset="1" stopColor="#1e3a8a" stopOpacity="0.95" />
          </linearGradient>
        </defs>
        <path d="M120 0 C 40 220, 260 380, 200 560 C 150 720, 60 800, 140 900 L800 900 L800 0 Z" fill="url(#wv1)" />
        <path d="M420 0 C 360 160, 560 260, 520 420 C 480 580, 380 640, 470 900 L800 900 L800 0 Z" fill="url(#wv2)" />
        <path d="M640 0 C 600 140, 760 220, 720 380 C 690 520, 620 600, 700 900 L800 900 L800 0 Z" fill="url(#wv3)" />
        <path d="M0 820 C 200 740, 420 900, 800 760 L800 900 L0 900 Z" fill="url(#wv2)" opacity="0.5" />
      </svg>
      {/* Dot clusters */}
      <div
        aria-hidden
        className="pointer-events-none absolute bottom-10 left-8 -z-10 hidden h-28 w-28 opacity-60 lg:block"
        style={{ backgroundImage: "radial-gradient(#60a5fa 2px, transparent 2px)", backgroundSize: "20px 20px" }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute right-10 top-8 -z-10 hidden h-24 w-32 opacity-70 lg:block"
        style={{ backgroundImage: "radial-gradient(#ffffff 2px, transparent 2px)", backgroundSize: "18px 18px" }}
      />

      {/* Brand panel */}
      <div className="hidden shrink-0 flex-col justify-between px-14 py-12 lg:flex lg:w-[54%]">
        <div>
          <img src="/logo-wordmark.png" alt="Work Assign Ai" className="h-auto w-72 rounded-lg object-contain" />
        </div>

        <div className="relative max-w-xl">
          {/* Mascot tile */}
          <div
            aria-hidden
            className="absolute -right-4 -top-6 hidden h-24 w-24 rotate-[10deg] items-center justify-center rounded-3xl border border-white/70 bg-gradient-to-br from-white/80 to-blue-200/70 text-blue-600 shadow-[0_18px_40px_-10px_rgba(37,99,235,0.45)] backdrop-blur xl:flex"
          >
            <Bot size={46} strokeWidth={1.75} />
          </div>

          <span className="inline-flex items-center gap-2 rounded-full border border-white/80 bg-white/80 px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-blue-700 shadow-[0_6px_20px_-8px_rgba(37,99,235,0.4)]">
            <AudioLines size={14} /> Voice + WhatsApp AI agents <ChevronRight size={14} />
          </span>
          <h1 className="mt-6 text-5xl font-extrabold leading-[1.1] tracking-[-0.03em] text-blue-950">
            Every call and chat,
            <br />
            <span className="relative inline-block text-blue-600">
              handled for you.
              <svg aria-hidden viewBox="0 0 260 12" className="absolute -bottom-2 left-0 h-3 w-full" preserveAspectRatio="none">
                <path d="M2 9 C 60 2, 140 2, 258 6" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
              </svg>
            </span>
          </h1>
          <p className="mt-6 max-w-md text-[17px] leading-[1.65] text-slate-600">
            Leads captured, appointments booked, follow-ups sent — and a human on your team steps in
            only when it matters.
          </p>

          <div className="mt-8 flex flex-col gap-3.5">
            <div className="flex items-center gap-4 rounded-2xl border border-white/80 bg-white/75 p-4 shadow-[0_10px_30px_-12px_rgba(37,99,235,0.35)] backdrop-blur">
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-600">
                <Phone size={24} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs font-bold text-blue-600">
                    <span className="h-1.5 w-1.5 rounded-full bg-blue-500" /> AI Voice call
                  </div>
                  <div className="flex items-center gap-1 font-mono text-[11px] text-slate-600">
                    <Phone size={11} /> 2:14
                  </div>
                </div>
                <p className="text-[13px] leading-relaxed text-slate-600">
                  <span className="font-semibold text-slate-900">Agent:</span> I can get you booked for Thursday at 2pm — does that work?
                </p>
                <p className="text-[13px] leading-relaxed text-slate-600">
                  <span className="font-semibold text-slate-900">Customer:</span> Yeah, that&apos;s perfect.
                </p>
              </div>
            </div>

            <div className="flex items-center gap-4 rounded-2xl border border-white/80 bg-white/75 p-4 shadow-[0_10px_30px_-12px_rgba(37,99,235,0.35)] backdrop-blur">
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
                <WhatsappIcon size={26} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs font-bold text-emerald-600">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> AI WhatsApp
                  </div>
                  <div className="flex items-center gap-1.5 text-[11px] text-slate-600">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> live
                  </div>
                </div>
                <p className="text-[13px] leading-relaxed text-slate-600">
                  <span className="font-semibold text-slate-900">Priya:</span> Hi, I saw your ad about the consultation offer.
                </p>
                <p className="text-[13px] leading-relaxed text-slate-600">
                  <span className="font-semibold text-slate-900">Agent:</span> Sure! Let me check availability for you now.
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs text-slate-600">
          <span>© 2026 Virox · Work Assign Ai</span>
          <span className="h-4 w-px bg-slate-300" />
          <span>Smarter Automation. Greater Productivity.</span>
        </div>
      </div>

      {/* Form panel */}
      <div className="relative flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-md rounded-[2rem] border border-white/80 bg-white/85 p-7 text-center shadow-[0_30px_80px_-20px_rgba(30,58,138,0.45)] backdrop-blur-xl sm:p-10">
          <div className="mb-8 lg:hidden">
            <img src="/logo-wordmark.png" alt="Work Assign Ai" className="mx-auto h-auto w-52 rounded-lg object-contain" />
          </div>
          {/* White disc so the full-colour mark keeps its contrast; thin blue
              ring + soft glow give it a polished, badge-like finish. */}
          <div className="mx-auto mb-5 flex h-20 w-20 items-center justify-center rounded-full bg-white shadow-[0_12px_32px_-10px_rgba(37,99,235,0.55)] ring-1 ring-blue-200">
            <img src="/icon-mark.png" alt="Work Assign Ai" className="h-12 w-12 object-contain" />
          </div>

          {orgChoices ? (
            <>
              <h2 className="text-xl font-bold text-slate-900">Choose an organization</h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">
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
                    className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 text-left text-sm shadow-card transition-colors hover:border-primary-400 hover:bg-white disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span className="font-medium text-slate-900">{org.org_name}</span>
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold capitalize text-slate-600">
                      {org.role}
                    </span>
                  </button>
                ))}
              </div>

              {error && (
                <p className="mt-4 flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-200">
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
                className="mt-5 text-xs font-medium text-primary-600 hover:text-primary-700"
              >
                ← Use a different token
              </button>
            </>
          ) : (
            <>
              <h2 className="text-3xl font-extrabold tracking-[-0.02em] text-blue-950">Welcome back</h2>
              <p className="mx-auto mt-3 max-w-sm text-[15px] leading-relaxed text-slate-600">
                Enter your organization&apos;s login token, or the shared admin token if you manage the
                platform.
              </p>

              <form onSubmit={handleSubmit} className="mt-7 flex flex-col gap-4 text-left" noValidate>
                <div>
                  <Label htmlFor="loginToken" className="text-slate-700">
                    Login token
                  </Label>
                  <div className="relative">
                    <Lock size={16} aria-hidden className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-600" />
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
                      className="border-slate-300 bg-white pl-10 pr-14 font-mono text-xs text-slate-900 shadow-none placeholder:text-slate-400"
                    />
                    <button
                      type="button"
                      onClick={() => setShowToken((s) => !s)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 font-mono text-[11px] text-slate-600 hover:text-slate-900"
                    >
                      {showToken ? "hide" : "show"}
                    </button>
                  </div>
                  <p className="mt-1.5 text-xs text-slate-600">
                    Tokens are never stored in plain text after sign in.
                  </p>
                </div>

                {error && (
                  <p className="flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-200">
                    <AlertCircle size={14} />
                    {error}
                  </p>
                )}

                <Button type="submit" variant="primary" className="mt-1 w-full gap-2 bg-gradient-to-r from-sky-500 to-blue-700 py-3 text-base shadow-[0_10px_25px_-8px_rgba(37,99,235,0.7)] hover:from-sky-600 hover:to-blue-800" loading={submitting}>
                  {!submitting && <LogIn size={15} />} {submitting ? "Signing in…" : "Sign in"}
                </Button>
              </form>

              <div className="mt-5 flex items-center justify-between text-xs">
                <Link
                  href="/forgot-token"
                  className="font-medium text-primary-600 hover:text-primary-700"
                >
                  Forgot your token?
                </Link>
                <span className="text-slate-600">No token? Ask your admin.</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
