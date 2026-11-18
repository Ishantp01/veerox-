"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { usePlanUsage } from "@/lib/hooks/usePayments";

// Only the metered usage resources get a banner at 100%.
const WATCHED: Record<string, { label: string; unit: string; action: string }> = {
  call_minutes: { label: "call minutes", unit: "min", action: "Calls are paused" },
  whatsapp_messages: { label: "WhatsApp messages", unit: "messages", action: "WhatsApp sending is paused" },
};

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/**
 * Red banner shown at the top of the dashboard once the active plan's call
 * minutes or WhatsApp messages are fully used (GET /payments/usage). The API
 * returns nothing for the owner org / orgs without a paid plan, so those never
 * see it. A failed request shows nothing.
 */
export function PlanLimitBanner() {
  const { user } = useAuth();
  const { data } = usePlanUsage();
  const canManage = user?.role !== "member" || !!user?.is_superuser;

  const reached = (data ?? []).filter(
    (u) => u.resource in WATCHED && u.limit !== null && u.used >= u.limit
  );
  if (reached.length === 0) return null;

  return (
    <div
      role="alert"
      className="mb-4 rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-100"
    >
      <p className="font-semibold">Plan limit reached</p>
      <ul className="mt-1 list-disc pl-5">
        {reached.map((u) => {
          const w = WATCHED[u.resource];
          return (
            <li key={u.resource}>
              You&apos;ve used all {fmt(u.limit as number)} {w.unit} of {w.label} in your plan. {w.action} until you
              buy an add-on, upgrade or renew.
            </li>
          );
        })}
      </ul>
      <p className="mt-2">
        {canManage ? (
          <Link href="/plans" className="font-medium underline underline-offset-2">
            Buy an add-on or renew your plan
          </Link>
        ) : (
          "Please ask your organization admin to upgrade or renew the plan."
        )}
      </p>
    </div>
  );
}
