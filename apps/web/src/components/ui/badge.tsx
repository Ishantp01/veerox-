import { HTMLAttributes, ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import {
  AlertCircle,
  CheckCircle2,
  Circle,
  CircleDot,
  Mic,
  MessageSquare,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Badge color language mirrors `styles/tokens.ts` (UI plan §8.2):
 * live=amber, ended=slate, success=emerald, danger=red,
 * voice=indigo, whatsapp=emerald, neutral=slate.
 */
export const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap ring-1 ring-inset",
  {
    variants: {
      variant: {
        live: "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-400 dark:ring-amber-500/20",
        ended: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700",
        success: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-400 dark:ring-emerald-500/20",
        danger: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-500/15 dark:text-red-400 dark:ring-red-500/20",
        voice: "bg-primary-50 text-primary-700 ring-primary-200 dark:bg-primary-500/15 dark:text-primary-400 dark:ring-primary-500/20",
        whatsapp: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-400 dark:ring-emerald-500/20",
        neutral: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  },
);

export type BadgeVariant = NonNullable<
  VariantProps<typeof badgeVariants>["variant"]
>;

/**
 * Default icon per variant so color is never the only signal (a11y, plan §10).
 * Callers can override with the `icon` prop or hide it with `icon={null}`.
 */
const DEFAULT_ICONS: Record<BadgeVariant, LucideIcon> = {
  live: CircleDot,
  ended: Circle,
  success: CheckCircle2,
  danger: AlertCircle,
  voice: Mic,
  whatsapp: MessageSquare,
  neutral: Circle,
};

export interface BadgeProps
  extends Omit<HTMLAttributes<HTMLSpanElement>, "children">,
    VariantProps<typeof badgeVariants> {
  children: ReactNode;
  /** Override the leading icon. Pass `null` to render text only. */
  icon?: LucideIcon | null;
}

export function Badge({
  variant = "neutral",
  icon,
  className,
  children,
  ...props
}: BadgeProps) {
  const resolved = variant ?? "neutral";
  const Icon = icon === null ? null : (icon ?? DEFAULT_ICONS[resolved]);
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props}>
      {Icon && <Icon size={12} aria-hidden className="shrink-0" />}
      {children}
    </span>
  );
}
