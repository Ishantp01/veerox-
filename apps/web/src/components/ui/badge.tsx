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
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
  {
    variants: {
      variant: {
        live: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
        ended: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
        success: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
        danger: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400",
        voice: "bg-primary-100 text-primary-700 dark:bg-primary-500/15 dark:text-primary-400",
        whatsapp: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
        neutral: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
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
