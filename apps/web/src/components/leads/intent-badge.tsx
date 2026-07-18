import {
  CalendarCheck,
  HelpCircle,
  LifeBuoy,
  ShoppingBag,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Visual treatment per lead intent. Color is never the sole signal — each
 * intent pairs a color bundle with a Lucide icon (a11y, UI plan §10).
 * Unknown / null intents fall back to a neutral slate "other" style.
 */
const INTENT_META: Record<string, { label: string; cls: string; icon: LucideIcon }> = {
  book_appointment: {
    label: "Book Appointment",
    cls: "bg-primary-50 text-primary-700 ring-primary-200 dark:bg-primary-500/15 dark:text-primary-400 dark:ring-primary-500/20",
    icon: CalendarCheck,
  },
  product_inquiry: {
    label: "Product Inquiry",
    cls: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-400 dark:ring-sky-500/20",
    icon: ShoppingBag,
  },
  support: {
    label: "Support",
    cls: "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-400 dark:ring-amber-500/20",
    icon: LifeBuoy,
  },
  other: {
    label: "Other",
    cls: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700",
    icon: HelpCircle,
  },
};

const FALLBACK = INTENT_META.other;

export interface IntentBadgeProps {
  intent: string | null | undefined;
  className?: string;
}

/** Pill describing a captured lead's intent. */
export function IntentBadge({ intent, className }: IntentBadgeProps) {
  const meta = (intent && INTENT_META[intent]) || FALLBACK;
  // Show the raw intent string when it isn't one we have a label for, so the
  // operator still sees the backend value rather than a misleading "Other".
  const label = intent && !INTENT_META[intent] ? intent : meta.label;
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap ring-1 ring-inset",
        meta.cls,
        className,
      )}
    >
      <Icon size={12} aria-hidden className="shrink-0" />
      {label}
    </span>
  );
}
