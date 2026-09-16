"use client";

import { AlertTriangle } from "lucide-react";
import { useAuth } from "@/lib/auth-context";

/**
 * Full-screen block rendered by (dashboard)/layout.tsx in place of the
 * dashboard shell once an org's license is suspended or expired — there is
 * no dismiss, no snooze, and no dashboard chrome underneath, since the API
 * refuses every org-scoped request anyway (apps/api/deps.py's
 * enforce_org_license). The only way out is the platform admin renewing or
 * reactivating the license from the Organizations page.
 */
export function LicenseLockedScreen({
  status,
  expiresAt,
}: {
  status: "suspended" | "expired" | string;
  expiresAt: string | null;
}) {
  const { logout } = useAuth();

  const title = status === "suspended" ? "Access suspended" : "License expired";
  const expiredOn = expiresAt ? new Date(expiresAt).toLocaleDateString() : null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas-950 bg-mesh-dark p-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-7 text-center shadow-card-lg">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-red-500/15 text-red-300">
          <AlertTriangle size={24} aria-hidden />
        </span>
        <h1 className="mt-4 text-2xl font-extrabold tracking-tight text-slate-50">{title}</h1>
        <p className="mt-2.5 text-sm leading-relaxed text-slate-400">
          {status === "suspended"
            ? "Your organization's access has been suspended by the platform admin."
            : expiredOn
              ? `Your organization's license expired on ${expiredOn}.`
              : "Your organization's license is no longer active."}{" "}
          Contact the platform admin to restore access.
        </p>
        <button
          type="button"
          onClick={() => logout()}
          className="mt-6 text-sm font-medium text-slate-400 underline underline-offset-4 transition-colors hover:text-slate-200"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
