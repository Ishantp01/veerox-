"use client";

import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AlertCircle, CheckCircle2, Info, X, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastVariant = "success" | "error" | "info";

export interface ToastOptions {
  title: string;
  description?: string;
  variant?: ToastVariant;
  /** Auto-dismiss delay in ms. Defaults to 4000. Pass 0 to disable. */
  duration?: number;
}

interface ToastItem extends Required<Omit<ToastOptions, "description">> {
  id: number;
  description?: string;
}

interface ToastContextValue {
  toast: (options: ToastOptions) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const VARIANT_META: Record<
  ToastVariant,
  { icon: LucideIcon; accent: string; iconColor: string }
> = {
  success: {
    icon: CheckCircle2,
    accent: "border-l-emerald-500",
    iconColor: "text-emerald-600 dark:text-emerald-400",
  },
  error: {
    icon: AlertCircle,
    accent: "border-l-red-500",
    iconColor: "text-red-600 dark:text-red-400",
  },
  info: { icon: Info, accent: "border-l-primary-500", iconColor: "text-primary-600 dark:text-primary-400" },
};

let nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  // Track pending timers so we can clear them on unmount / manual dismiss.
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const toast = useCallback(
    ({ title, description, variant = "info", duration = 4000 }: ToastOptions) => {
      const id = nextId++;
      setToasts((prev) => [...prev, { id, title, description, variant, duration }]);
      if (duration > 0) {
        const timer = setTimeout(() => dismiss(id), duration);
        timers.current.set(id, timer);
      }
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((t) => clearTimeout(t));
      map.clear();
    };
  }, []);

  const value = useMemo(() => ({ toast, dismiss }), [toast, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <Toaster toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within a <ToastProvider>");
  }
  return ctx;
}

interface ToasterProps {
  toasts: ToastItem[];
  onDismiss: (id: number) => void;
}

/** Fixed render region for toasts. Announced politely to screen readers. */
export function Toaster({ toasts, onDismiss }: ToasterProps) {
  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
    >
      {toasts.map((t) => {
        const meta = VARIANT_META[t.variant];
        const Icon = meta.icon;
        return (
          <div
            key={t.id}
            role={t.variant === "error" ? "alert" : "status"}
            className={cn(
              "pointer-events-auto flex items-start gap-3 rounded-xl border border-slate-200 border-l-4 bg-white px-4 py-3 shadow-elevated-lg dark:border-slate-700 dark:bg-slate-900",
              meta.accent,
            )}
          >
            <Icon size={18} className={cn("mt-0.5 shrink-0", meta.iconColor)} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t.title}</p>
              {t.description && (
                <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{t.description}</p>
              )}
            </div>
            <button
              type="button"
              onClick={() => onDismiss(t.id)}
              aria-label="Dismiss notification"
              className="shrink-0 rounded-md p-0.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:hover:bg-slate-800 dark:hover:text-slate-300"
            >
              <X size={16} aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
