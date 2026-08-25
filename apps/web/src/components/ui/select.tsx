"use client";

import {
  Children,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
  isValidElement,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface ParsedOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

export interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  /** `<option value="...">Label</option>` elements, same shape as a native select. */
  children: ReactNode;
  id?: string;
  className?: string;
  disabled?: boolean;
  "aria-label"?: string;
  /** Native hover tooltip on the trigger button. */
  title?: string;
}

function parseOptions(children: ReactNode): ParsedOption[] {
  return Children.toArray(children)
    .filter(isValidElement)
    .map((child) => {
      const props = child.props as { value?: string; children?: ReactNode; disabled?: boolean };
      return { value: props.value ?? "", label: props.children, disabled: props.disabled };
    });
}

/**
 * Custom-styled drop-in replacement for `<select>` — matches theme colors and
 * (unlike a native select) keeps its option list themed instead of falling
 * back to the OS-native popup. Renders the list in a portal, positioned from
 * the trigger's rect, so it isn't clipped by scrollable table containers.
 */
export function Select({
  value,
  onChange,
  children,
  id,
  className,
  disabled,
  "aria-label": ariaLabel,
  title,
}: SelectProps) {
  const options = parseOptions(children);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listboxId = useId();
  const selected = options.find((o) => o.value === value);
  const isFullWidth = /\bw-full\b/.test(className ?? "");

  useLayoutEffect(() => {
    if (!open) return;
    function updateRect() {
      const r = buttonRef.current?.getBoundingClientRect();
      if (r) setRect({ top: r.bottom + 4, left: r.left, width: r.width });
    }
    updateRect();
    window.addEventListener("scroll", updateRect, true);
    window.addEventListener("resize", updateRect);
    return () => {
      window.removeEventListener("scroll", updateRect, true);
      window.removeEventListener("resize", updateRect);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const idx = options.findIndex((o) => o.value === value);
    setActiveIndex(idx >= 0 ? idx : 0);
    listRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onDocMouseDown(e: MouseEvent) {
      if (
        buttonRef.current?.contains(e.target as Node) ||
        listRef.current?.contains(e.target as Node)
      ) {
        return;
      }
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  function commit(idx: number) {
    const opt = options[idx];
    if (!opt || opt.disabled) return;
    onChange(opt.value);
    setOpen(false);
    buttonRef.current?.focus();
  }

  function onButtonKeyDown(e: ReactKeyboardEvent) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen(true);
    }
  }

  function onListKeyDown(e: ReactKeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, options.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      commit(activeIndex);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div className={cn("relative", isFullWidth ? "w-full" : "inline-block max-w-full align-middle")}>
      <button
        ref={buttonRef}
        type="button"
        id={id}
        disabled={disabled}
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onButtonKeyDown}
        className={cn(
          "flex items-center justify-between gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-left text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100",
          className,
        )}
      >
        <span className="truncate">{selected?.label}</span>
        <ChevronDown size={15} className="shrink-0 text-slate-400" aria-hidden />
      </button>
      {open &&
        rect &&
        createPortal(
          <ul
            id={listboxId}
            role="listbox"
            ref={listRef}
            tabIndex={-1}
            onKeyDown={onListKeyDown}
            style={{ position: "fixed", top: rect.top, left: rect.left, width: rect.width }}
            className="z-50 max-h-60 overflow-auto rounded-xl border border-slate-200 bg-white py-1 text-sm shadow-card-lg focus-visible:outline-none dark:border-slate-700 dark:bg-slate-900"
          >
            {options.map((opt, idx) => (
              <li
                key={opt.value}
                role="option"
                aria-selected={opt.value === value}
                onMouseEnter={() => setActiveIndex(idx)}
                onClick={() => commit(idx)}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-2 px-3.5 py-2 text-slate-700 dark:text-slate-200",
                  idx === activeIndex && "bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300",
                  opt.value === value && "font-semibold",
                  opt.disabled && "pointer-events-none opacity-50",
                )}
              >
                <span className="truncate">{opt.label}</span>
                {opt.value === value && <Check size={14} className="shrink-0" aria-hidden />}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  );
}
