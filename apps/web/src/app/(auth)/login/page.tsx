"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Button from "@/components/ui/button";
import { LogIn, AlertCircle, Sparkles } from "lucide-react";

const TOKEN_KEY = "veerox_admin_token";

/**
 * Check a candidate token against the backend before persisting it. Can't
 * use `apiFetch` here — it always reads the *stored* token from
 * localStorage, but we need to test one that hasn't been saved yet.
 */
async function validateToken(candidate: string): Promise<boolean> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
  const res = await fetch(`${base}/admin/settings`, {
    headers: { "X-Admin-Token": candidate },
  });
  return res.ok;
}

export default function LoginPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed) {
      setError("Please enter your admin token.");
      return;
    }

    setChecking(true);
    setError(null);
    try {
      const valid = await validateToken(trimmed);
      if (!valid) {
        setError("That token was rejected by the server. Double-check and try again.");
        return;
      }
      localStorage.setItem(TOKEN_KEY, trimmed);
      router.push("/");
    } catch {
      setError("Couldn't reach the API to verify the token. Is the backend running?");
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-gradient-to-br from-slate-100 to-primary-50 dark:from-slate-950 dark:to-slate-900">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-400 via-primary-600 to-violet-700 text-white shadow-elevated-lg shadow-primary-900/20 mb-4">
            <Sparkles size={26} strokeWidth={2.25} />
          </div>
          <h1 className="text-2xl font-extrabold text-slate-900 dark:text-slate-50 tracking-tight">Veerox AI</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Admin Dashboard</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-elevated-lg dark:border-slate-800 dark:bg-slate-900">
          <h2 className="text-base font-bold text-slate-800 dark:text-slate-100 mb-1">Sign in</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">Enter your admin token to continue.</p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div>
              <label htmlFor="token" className="block text-xs font-semibold uppercase tracking-widest text-slate-400 dark:text-slate-500 mb-2">
                Admin Token
              </label>
              <input
                id="token"
                type="password"
                autoComplete="current-password"
                value={token}
                onChange={(e) => { setToken(e.target.value); setError(null); }}
                placeholder="Paste token here"
                aria-invalid={error ? true : undefined}
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500 aria-[invalid=true]:border-red-400 aria-[invalid=true]:focus:ring-red-500"
              />
            </div>

            {error && (
              <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2 flex items-center gap-2 dark:bg-red-500/10 dark:text-red-400">
                <AlertCircle size={14} />{error}
              </p>
            )}

            <Button type="submit" variant="default" className="w-full py-2.5 mt-1 gap-2" loading={checking}>
              {!checking && <LogIn size={15} />} {checking ? "Checking…" : "Sign in"}
            </Button>
          </form>

          <p className="mt-5 text-xs text-slate-400 dark:text-slate-500 text-center">
            Token is sent as <code className="font-mono bg-slate-100 dark:bg-slate-800 px-1 rounded">X-Admin-Token</code> on every request.
          </p>
        </div>
      </div>
    </div>
  );
}
