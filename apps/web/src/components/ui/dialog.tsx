"use client";

import {
  ButtonHTMLAttributes,
  HTMLAttributes,
  ReactNode,
  cloneElement,
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
} from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface DialogContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  titleId: string;
}

const DialogContext = createContext<DialogContextValue | null>(null);

function useDialogContext(component: string): DialogContextValue {
  const ctx = useContext(DialogContext);
  if (!ctx) {
    throw new Error(`<${component}> must be used within a <Dialog>`);
  }
  return ctx;
}

export interface DialogProps {
  /** Controlled open state. */
  open: boolean;
  /** Called when the dialog requests to change open state (Escape/backdrop/close). */
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}

export function Dialog({ open, onOpenChange, children }: DialogProps) {
  const titleId = useId();
  return (
    <DialogContext.Provider value={{ open, setOpen: onOpenChange, titleId }}>
      {children}
    </DialogContext.Provider>
  );
}

export interface DialogTriggerProps {
  /** A single focusable child that opens the dialog when clicked. */
  children: ReactNode;
}

/**
 * Renders its child and wires an onClick that opens the dialog. The child
 * must accept an `onClick` prop (e.g. a Button or native element).
 */
export function DialogTrigger({ children }: DialogTriggerProps) {
  const { setOpen } = useDialogContext("DialogTrigger");
  if (!isValidElement<{ onClick?: (e: unknown) => void }>(children)) {
    return <>{children}</>;
  }
  return cloneElement(children, {
    onClick: (e: unknown) => {
      children.props.onClick?.(e);
      setOpen(true);
    },
  });
}

export interface DialogContentProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';

export function DialogContent({
  children,
  className,
  ...props
}: DialogContentProps) {
  const { open, setOpen, titleId } = useDialogContext("DialogContent");
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;
    // Focus the panel itself on open (plan §10 keyboard access).
    panelRef.current?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
        return;
      }
      if (e.key === "Tab") {
        const panel = panelRef.current;
        if (!panel) return;
        const items = Array.from(
          panel.querySelectorAll<HTMLElement>(FOCUSABLE),
        ).filter((el) => el.offsetParent !== null);
        if (items.length === 0) {
          e.preventDefault();
          panel.focus();
          return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        const active = document.activeElement;
        if (e.shiftKey && (active === first || active === panel)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener("keydown", onKeyDown);
    // Lock background scroll while the modal is open.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prevOverflow;
      previouslyFocused.current?.focus?.();
    };
  }, [open, setOpen]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onMouseDown={(e) => {
        // Close only when the backdrop itself is clicked (not the panel).
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm dark:bg-black/60" aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn(
          "relative z-10 w-full max-w-lg rounded-2xl border border-slate-200 bg-white shadow-elevated-lg focus-visible:outline-none dark:border-slate-800 dark:bg-slate-900",
          className,
        )}
        {...props}
      >
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Close dialog"
          className="absolute right-4 top-4 rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:hover:bg-slate-800 dark:hover:text-slate-300"
        >
          <X size={18} aria-hidden />
        </button>
        {children}
      </div>
    </div>
  );
}

export function DialogTitle({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  const { titleId } = useDialogContext("DialogTitle");
  return (
    <h2
      id={titleId}
      className={cn(
        "px-6 pt-6 pr-12 text-lg font-semibold text-slate-900 dark:text-slate-100",
        className,
      )}
      {...props}
    >
      {children}
    </h2>
  );
}

export function DialogBody({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("px-6 py-4 text-sm text-slate-600 dark:text-slate-400", className)} {...props}>
      {children}
    </div>
  );
}

export function DialogFooter({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center justify-end gap-3 border-t border-slate-100 px-6 py-4 dark:border-slate-800",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/** A button that closes the nearest dialog (for use in DialogFooter). */
export function DialogClose({
  children,
  onClick,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  const { setOpen } = useDialogContext("DialogClose");
  return (
    <button
      type="button"
      onClick={(e) => {
        onClick?.(e);
        setOpen(false);
      }}
      {...props}
    >
      {children}
    </button>
  );
}
