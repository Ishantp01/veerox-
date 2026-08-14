"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import Button from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { LogIn, AlertCircle } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { login as loginRequest } from "@/lib/hooks/useAuthApi";

const tokenSchema = z.object({
  loginToken: z.string().trim().min(1, "Token is required"),
});

export default function LoginPage() {
  const router = useRouter();
  const { login, loginAdmin } = useAuth();
  const [loginToken, setLoginToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
    <div className="flex min-h-full items-center justify-center">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-400 to-primary-600 text-white mb-4 shadow-glow-lg">
            <span className="text-2xl font-black leading-none">V</span>
          </div>
          <h1 className="text-2xl font-extrabold text-white tracking-tight">VEEROX</h1>
          <p className="text-[11px] font-semibold text-slate-400 mt-1.5 uppercase tracking-[0.2em]">Software</p>
          <p className="text-xs text-slate-500 mt-1">Built for Success!</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-8 shadow-card-lg backdrop-blur-xl">
          <h2 className="text-base font-bold text-slate-100 mb-1">Sign in</h2>
          <p className="text-sm text-slate-400 mb-6">
            Enter your org login token, or the shared admin token for the owner org.
          </p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
            <div>
              <Label htmlFor="loginToken" className="!text-slate-400">Login token</Label>
              <Input
                id="loginToken"
                type="password"
                autoComplete="current-password"
                value={loginToken}
                onChange={(e) => { setLoginToken(e.target.value); setError(null); }}
                placeholder="Paste your login or admin token"
                aria-invalid={error ? true : undefined}
                className="!bg-white/5 !border-white/10 !text-slate-100 font-mono text-xs"
              />
            </div>

            {error && (
              <p className="text-sm text-red-300 bg-red-500/10 ring-1 ring-red-500/20 rounded-lg px-3 py-2 flex items-center gap-2">
                <AlertCircle size={14} />{error}
              </p>
            )}

            <Button type="submit" variant="default" className="w-full py-2.5 mt-1 gap-2" loading={submitting}>
              {!submitting && <LogIn size={15} />} {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <p className="mt-5 text-xs text-slate-500 text-center">
            Don&apos;t have a token? Ask your organization&apos;s admin.
          </p>
        </div>
      </div>
    </div>
  );
}
