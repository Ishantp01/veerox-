"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { z } from "zod";
import Button from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Send, ArrowLeft, CheckCircle2 } from "lucide-react";
import { forgotToken } from "@/lib/hooks/useAuthApi";

const identifierSchema = z.object({
  identifier: z.string().trim().min(1, "Enter your email or mobile number"),
});

const GENERIC_MESSAGE = "If an account matches, a new login token has been sent.";

export default function ForgotTokenPage() {
  const [identifier, setIdentifier] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const parsed = identifierSchema.safeParse({ identifier });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Enter your email or mobile number.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await forgotToken(identifier.trim());
    } catch {
      // Same generic outcome whether the request succeeded, found no match,
      // or failed to reach the API — showing anything else here would leak
      // which emails/numbers have accounts.
    } finally {
      setSubmitting(false);
      setSent(true);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas-950 bg-mesh-dark p-6">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-400 to-primary-600 text-white mb-4 shadow-glow-lg">
            <span className="text-2xl font-black leading-none">V</span>
          </div>
          <h1 className="text-2xl font-extrabold text-white tracking-tight">VEEROX</h1>
          <p className="text-[11px] font-semibold text-slate-400 mt-1.5 uppercase tracking-[0.2em]">Software</p>
          <p className="text-xs text-slate-500 mt-1">Built for Success!</p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-8 shadow-card-lg backdrop-blur-xl">
          {sent ? (
            <>
              <div className="flex flex-col items-center text-center gap-3 mb-2">
                <CheckCircle2 size={28} className="text-primary-400" />
                <h2 className="text-base font-bold text-slate-100">Check your email or phone</h2>
                <p className="text-sm text-slate-400">{GENERIC_MESSAGE}</p>
              </div>
              <Link
                href="/login"
                className="mt-5 flex items-center justify-center gap-2 text-xs text-primary-400 hover:text-primary-300"
              >
                <ArrowLeft size={13} /> Back to sign in
              </Link>
            </>
          ) : (
            <>
              <h2 className="text-base font-bold text-slate-100 mb-1">Forgot your token?</h2>
              <p className="text-sm text-slate-400 mb-6">
                Enter your email or mobile number and we&apos;ll send a new login token. Your
                previous token will stop working.
              </p>

              <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
                <div>
                  <Label htmlFor="identifier" className="!text-slate-400">Email or mobile number</Label>
                  <Input
                    id="identifier"
                    type="text"
                    autoComplete="username"
                    value={identifier}
                    onChange={(e) => { setIdentifier(e.target.value); setError(null); }}
                    placeholder="you@example.com or +919876543210"
                    aria-invalid={error ? true : undefined}
                    className="!bg-white/5 !border-white/10 !text-slate-100 text-sm"
                  />
                </div>

                {error && (
                  <p className="text-sm text-red-300 bg-red-500/10 ring-1 ring-red-500/20 rounded-lg px-3 py-2">
                    {error}
                  </p>
                )}

                <Button type="submit" variant="default" className="w-full py-2.5 mt-1 gap-2" loading={submitting}>
                  {!submitting && <Send size={15} />} {submitting ? "Sending…" : "Send new token"}
                </Button>
              </form>

              <Link
                href="/login"
                className="mt-5 flex items-center justify-center gap-2 text-xs text-slate-500 hover:text-slate-300"
              >
                <ArrowLeft size={13} /> Back to sign in
              </Link>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
