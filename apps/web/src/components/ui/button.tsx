import { ButtonHTMLAttributes, forwardRef } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { Spinner } from "./spinner";

/**
 * Button variants via `cva`. `default` is retained as an alias of `primary`
 * for backward compatibility with pages built before the variant system
 * (they pass `variant="default"`). New code should prefer `primary`.
 */
export const buttonVariants = cva(
  "inline-flex items-center justify-center rounded-xl font-medium transition-all duration-150 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-slate-900 disabled:pointer-events-none disabled:opacity-50 disabled:active:scale-100",
  {
    variants: {
      variant: {
        primary:
          "bg-gradient-to-b from-primary-500 to-primary-600 text-white shadow-glow hover:shadow-glow-lg hover:from-primary-500 hover:to-primary-700",
        default:
          "bg-gradient-to-b from-primary-500 to-primary-600 text-white shadow-glow hover:shadow-glow-lg hover:from-primary-500 hover:to-primary-700",
        secondary:
          "bg-slate-100 text-slate-700 hover:bg-slate-200 focus-visible:ring-slate-400 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700",
        ghost:
          "bg-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-900 focus-visible:ring-slate-400 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100",
        danger:
          "bg-gradient-to-b from-red-500 to-red-600 text-white shadow-elevated hover:from-red-500 hover:to-red-700 focus-visible:ring-red-500",
        outline:
          "border border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50 focus-visible:ring-slate-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800",
      },
      size: {
        sm: "px-3 py-1.5 text-xs gap-1.5",
        md: "px-4 py-2.5 text-sm gap-2",
        lg: "px-6 py-3 text-base gap-2",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** When true, shows a Spinner and disables the button. */
  loading?: boolean;
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, loading = false, disabled, className, children, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    >
      {loading && <Spinner size={16} />}
      {children}
    </button>
  );
});

export default Button;
export { Button };
