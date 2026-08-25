"use client";

import { ReactNode, createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import Button from "./button";
import { Dialog, DialogContent, DialogTitle, DialogBody, DialogFooter } from "./dialog";

export interface ConfirmOptions {
  title?: string;
  description: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** "danger" red-styles the confirm button, for destructive actions (the common case). */
  variant?: "danger" | "primary";
}

interface PendingConfirm extends ConfirmOptions {
  resolve: (confirmed: boolean) => void;
}

interface ConfirmContextValue {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null);

/**
 * App-wide replacement for window.confirm() — a Promise-based confirm
 * dialog styled like the rest of the UI instead of the browser's native
 * chrome. Mounted once in providers.tsx; call sites just `await confirm(...)`.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  // Guards against the resolve() in settle() running twice (once from a
  // button click, once from the Dialog's onOpenChange(false) it triggers).
  const settledRef = useRef(false);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      settledRef.current = false;
      setPending({ ...options, resolve });
    });
  }, []);

  const settle = useCallback(
    (confirmed: boolean) => {
      if (settledRef.current) return;
      settledRef.current = true;
      pending?.resolve(confirmed);
      setPending(null);
    },
    [pending],
  );

  const value = useMemo(() => ({ confirm }), [confirm]);

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      <Dialog open={pending !== null} onOpenChange={(open) => !open && settle(false)}>
        <DialogContent className="max-w-sm">
          <DialogTitle className="flex items-center gap-2.5">
            {pending?.variant !== "primary" && (
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-red-50 text-red-500 dark:bg-red-500/10 dark:text-red-400">
                <AlertTriangle size={15} aria-hidden />
              </span>
            )}
            {pending?.title ?? "Are you sure?"}
          </DialogTitle>
          <DialogBody>{pending?.description}</DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => settle(false)}>
              {pending?.cancelLabel ?? "Cancel"}
            </Button>
            <Button
              variant={pending?.variant === "primary" ? "primary" : "danger"}
              onClick={() => settle(true)}
            >
              {pending?.confirmLabel ?? "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): (options: ConfirmOptions) => Promise<boolean> {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error("useConfirm must be used within a <ConfirmProvider>");
  }
  return ctx.confirm;
}
