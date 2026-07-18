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
    <div className="flex min-h-full items-center justify-center">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-400 to-primary-600 text-white mb-4 shadow-glow-lg">
            <Sparkles size={22} strokeWidth={2.25} />
          </div>
          <h1 className="text-xl font-semibold text-white tracking-tight">Veerox AI</h1>
          <p className="text-sm text-slate-400 mt-1">Admin Dashboard</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-8 shadow-card-lg backdrop-blur-xl">
          <h2 className="text-base font-bold text-slate-100 mb-1">Sign in</h2>
          <p className="text-sm text-slate-400 mb-6">Enter your admin token to continue.</p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div>
              <label htmlFor="token" className="block text-xs font-semibold uppercase tracking-widest text-slate-500 mb-2">
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
                className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm text-slate-100 shadow-sm placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition aria-[invalid=true]:border-red-500/50 aria-[invalid=true]:focus:ring-red-500"
              />
            </div>

            {error && (
              <p className="text-sm text-red-300 bg-red-500/10 ring-1 ring-red-500/20 rounded-lg px-3 py-2 flex items-center gap-2">
                <AlertCircle size={14} />{error}
              </p>
            )}

            <Button type="submit" variant="default" className="w-full py-2.5 mt-1 gap-2" loading={checking}>
              {!checking && <LogIn size={15} />} {checking ? "Checking…" : "Sign in"}
            </Button>
          </form>

          <p className="mt-5 text-xs text-slate-500 text-center">
            Token is sent as <code className="font-mono bg-white/5 px-1 rounded">X-Admin-Token</code> on every request.
          </p>
        </div>
      </div>
    </div>
  );
}
