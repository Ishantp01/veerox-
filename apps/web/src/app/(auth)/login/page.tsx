"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { z } from "zod";
import { useTheme } from "next-themes";
import { LogIn, AlertCircle, Sun, Moon } from "lucide-react";
import Button from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";
import { login as loginRequest } from "@/lib/hooks/useAuthApi";

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
  const isDark = resolvedTheme === "dark";

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
      const session = await loginRequest(loginToken.trim());
      login(session);
      router.push("/");
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

  return (
    <div className="flex min-h-screen">
      {/* Brand panel */}
      <div className="hidden shrink-0 flex-col justify-between border-r border-slate-200 bg-white px-14 py-14 dark:border-slate-800 dark:bg-slate-950 lg:flex lg:w-[46%]">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-glow">
            <span className="text-base font-black leading-none">V</span>
          </div>
          <div className="leading-tight">
            <div className="text-[17px] font-extrabold tracking-wide text-slate-900 dark:text-white">VEEROX</div>
            <div className="text-[10px] font-semibold tracking-[0.18em] text-slate-400 dark:text-slate-500">SOFTWARE</div>
          </div>
        </div>

        <div className="mt-14 max-w-md">
          <h1 className="text-2xl font-bold leading-snug tracking-tight text-slate-900 dark:text-white">
            One dashboard for every call and chat your agent handles.
          </h1>
          <p className="mt-3 text-sm leading-relaxed text-slate-500 dark:text-slate-400">
            Voice calls and WhatsApp conversations, leads captured, appointments booked — escalated
            to your team only when it matters.
          </p>

          <div className="mt-9 flex flex-col gap-3">
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card dark:border-slate-800 dark:bg-slate-900">
              <div className="mb-2.5 flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-bold text-primary-600 dark:text-primary-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-primary-500" /> Voice
                </div>
                <div className="font-mono text-[11px] text-slate-400 dark:text-slate-500">
                  +1 (415) 555-0138 · 2:14
                </div>
              </div>
              <p className="mb-1 text-[13px] leading-relaxed text-slate-500 dark:text-slate-400">
                <span className="font-medium text-slate-700 dark:text-slate-200">Agent:</span> I can
                get you booked for Thursday at 2pm — does that work?
              </p>
              <p className="text-[13px] leading-relaxed text-slate-500 dark:text-slate-400">
                Customer: Yeah, that&apos;s perfect.
              </p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card dark:border-slate-800 dark:bg-slate-900">
              <div className="mb-2.5 flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-bold text-emerald-600 dark:text-emerald-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> WhatsApp
                </div>
                <div className="flex items-center gap-1.5 font-mono text-[11px] text-slate-400 dark:text-slate-500">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" /> live
                </div>
              </div>
              <p className="mb-1 text-[13px] leading-relaxed text-slate-500 dark:text-slate-400">
                Priya M.: Hi, I saw your ad about the consultation offer.
              </p>
              <p className="text-[13px] leading-relaxed text-slate-500 dark:text-slate-400">
                <span className="font-medium text-slate-700 dark:text-slate-200">Agent:</span> Sure!
                Let me check availability for you now.
              </p>
            </div>
          </div>

          <p className="mt-6 text-xs leading-relaxed text-slate-400 dark:text-slate-500">
            Every conversation, on every channel, in one place — with a human ready to step in the
            moment your agent needs one.
          </p>
        </div>

        <div className="text-xs text-slate-400 dark:text-slate-500">© 2026 Veerox AI · Built for Success!</div>
      </div>

      {/* Form panel */}
      <div className="relative flex flex-1 items-center justify-center bg-slate-50 px-6 py-12 dark:bg-slate-900">
        <button
          type="button"
          onClick={() => setTheme(isDark ? "light" : "dark")}
          aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
          className="absolute right-6 top-6 flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-400 shadow-sm transition-colors hover:text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-500 dark:hover:text-slate-300"
        >
          {isDark ? <Moon size={15} /> : <Sun size={15} />}
        </button>

        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-glow">
              <span className="text-base font-black leading-none">V</span>
            </div>
            <div className="text-[17px] font-extrabold tracking-wide text-slate-900 dark:text-white">VEEROX</div>
          </div>

          <h2 className="text-xl font-bold text-slate-900 dark:text-white">Sign in</h2>
          <p className="mt-2 text-sm leading-relaxed text-slate-500 dark:text-slate-400">
            Enter your organization&apos;s login token, or the shared admin token if you manage the
            platform.
          </p>

          <form onSubmit={handleSubmit} className="mt-7 flex flex-col gap-4" noValidate>
            <div>
              <Label htmlFor="loginToken">Login token</Label>
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
                  className="pr-14 font-mono text-xs"
                />
                <button
                  type="button"
                  onClick={() => setShowToken((s) => !s)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 font-mono text-[11px] text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
                >
                  {showToken ? "hide" : "show"}
                </button>
              </div>
              <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                Tokens are never stored in plain text after sign in.
              </p>
            </div>

            {error && (
              <p className="flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 ring-1 ring-red-200 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-500/20">
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
              className="font-medium text-primary-600 hover:text-primary-700 dark:text-primary-400 dark:hover:text-primary-300"
            >
              Forgot your token?
            </Link>
            <span className="text-slate-400 dark:text-slate-500">No token? Ask your admin.</span>
          </div>
        </div>
      </div>
    </div>
  );
}
