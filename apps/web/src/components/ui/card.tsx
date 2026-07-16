import { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Card({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-slate-200 bg-white shadow-elevated dark:border-slate-800 dark:bg-slate-900 dark:shadow-none",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("border-b border-slate-100 px-6 py-4 dark:border-slate-800", className)}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardTitle({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn("text-base font-semibold text-slate-800 dark:text-slate-100", className)}
      {...props}
    >
      {children}
    </h3>
  );
}

export function CardContent({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("px-6 py-5", className)} {...props}>
      {children}
    </div>
  );
}

export function CardFooter({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 border-t border-slate-100 px-6 py-4 dark:border-slate-800",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
