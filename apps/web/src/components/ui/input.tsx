import {
  InputHTMLAttributes,
  LabelHTMLAttributes,
  TextareaHTMLAttributes,
  forwardRef,
} from "react";
import { cn } from "@/lib/utils";

const FIELD_BASE =
  "w-full rounded-xl border bg-white px-3.5 py-2.5 text-sm text-slate-800 placeholder:text-slate-400 shadow-sm transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:focus-visible:ring-offset-slate-900 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500";

/** Slate border by default; red border + ring when `aria-invalid`. */
const FIELD_STATE =
  "border-slate-300 aria-[invalid=true]:border-red-400 aria-[invalid=true]:focus-visible:ring-red-500 dark:border-slate-700 dark:aria-[invalid=true]:border-red-500";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, type = "text", ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      type={type}
      className={cn(FIELD_BASE, FIELD_STATE, className)}
      {...props}
    />
  );
});

export interface TextareaProps
  extends TextareaHTMLAttributes<HTMLTextAreaElement> {}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea({ className, rows = 4, ...props }, ref) {
    return (
      <textarea
        ref={ref}
        rows={rows}
        className={cn(FIELD_BASE, FIELD_STATE, "resize-y", className)}
        {...props}
      />
    );
  },
);

export interface LabelProps extends LabelHTMLAttributes<HTMLLabelElement> {
  /** Renders a red asterisk to mark the field as required. */
  required?: boolean;
}

export function Label({ className, required, children, ...props }: LabelProps) {
  return (
    <label
      className={cn(
        "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300",
        className,
      )}
      {...props}
    >
      {children}
      {required && (
        <span className="ml-0.5 text-red-500" aria-hidden>
          *
        </span>
      )}
    </label>
  );
}
