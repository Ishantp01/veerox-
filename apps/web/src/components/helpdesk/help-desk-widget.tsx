"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, GripVertical, Send, Sparkles, User, X } from "lucide-react";
import { z } from "zod";

import { useHelpDeskChat } from "@/lib/hooks/useHelpDesk";
import type { HelpDeskMessage } from "@/lib/types";

const draftSchema = z.string().trim().min(1).max(4000);

const GREETING: HelpDeskMessage = {
  role: "assistant",
  content:
    "Hi! I'm the Veerox help assistant. Ask me anything about using the dashboard — calling, WhatsApp, billing, campaigns, and more.",
};

const SUGGESTIONS = [
  "How do I connect WhatsApp?",
  "Where do I see my usage this month?",
  "How does automated follow-up work?",
];

// Anchored by distance from the bottom-right corner (matches the original
// bottom-6 right-6 default) rather than top-left, so the chat panel keeps
// opening *above* the launcher bubble no matter where it's been dragged to.
type Offset = { right: number; bottom: number };

const DEFAULT_OFFSET: Offset = { right: 24, bottom: 24 };
const STORAGE_KEY = "veerox:helpdesk-widget-pos";
const DRAG_THRESHOLD_PX = 4;

function clampOffset(offset: Offset, size: number): Offset {
  const maxRight = Math.max(8, window.innerWidth - size - 8);
  const maxBottom = Math.max(8, window.innerHeight - size - 8);
  return {
    right: Math.min(Math.max(offset.right, 8), maxRight),
    bottom: Math.min(Math.max(offset.bottom, 8), maxBottom),
  };
}

/**
 * Drag-to-reposition for the launcher bubble (and, by extension, the panel
 * that anchors off it). Pointer-events-based so it works for mouse and
 * touch alike; a small movement threshold keeps a plain click from being
 * swallowed as a drag. Position persists across reloads via localStorage.
 */
function useDraggableOffset(size: number) {
  const [offset, setOffset] = useState<Offset>(DEFAULT_OFFSET);
  const dragState = useRef<{ startX: number; startY: number; startOffset: Offset; dragged: boolean } | null>(
    null
  );
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) setOffset(clampOffset(JSON.parse(saved), size));
    } catch {
      // ignore malformed/blocked storage
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    function handleResize() {
      setOffset((prev) => clampOffset(prev, size));
    }
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [size]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      dragState.current = { startX: e.clientX, startY: e.clientY, startOffset: offset, dragged: false };
      setDragging(true);

      function handleMove(ev: PointerEvent) {
        const state = dragState.current;
        if (!state) return;
        const dx = ev.clientX - state.startX;
        const dy = ev.clientY - state.startY;
        if (!state.dragged && Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) state.dragged = true;
        if (state.dragged) {
          setOffset(
            clampOffset({ right: state.startOffset.right - dx, bottom: state.startOffset.bottom - dy }, size)
          );
        }
      }

      function handleUp() {
        window.removeEventListener("pointermove", handleMove);
        window.removeEventListener("pointerup", handleUp);
        setDragging(false);
        setOffset((current) => {
          try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(current));
          } catch {
            // ignore
          }
          return current;
        });
      }

      window.addEventListener("pointermove", handleMove);
      window.addEventListener("pointerup", handleUp);
    },
    [offset, size]
  );

  // Consumed on click to tell a genuine click apart from the end of a drag.
  const consumeWasDragged = useCallback(() => {
    const was = dragState.current?.dragged ?? false;
    if (dragState.current) dragState.current.dragged = false;
    return was;
  }, []);

  return { offset, dragging, onPointerDown, consumeWasDragged };
}

function TypingDots() {
  return (
    <span className="flex items-center gap-1 py-1">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.3s] dark:bg-slate-500" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.15s] dark:bg-slate-500" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 dark:bg-slate-500" />
    </span>
  );
}

function MessageBubble({ message }: { message: HelpDeskMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex items-end gap-2 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
      <div
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
          isUser
            ? "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
            : "bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-sm"
        }`}
      >
        {isUser ? <User size={13} aria-hidden /> : <Bot size={14} aria-hidden />}
      </div>
      <div
        className={`max-w-[78%] whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed shadow-sm ${
          isUser
            ? "rounded-br-md bg-gradient-to-br from-primary-500 to-primary-600 text-white"
            : "rounded-bl-md border border-slate-200/80 bg-white text-slate-700 dark:border-slate-700/80 dark:bg-slate-800 dark:text-slate-200"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

/**
 * Floating help-desk chat widget, available on every dashboard page for
 * every authenticated org member (team members included, not just admins).
 * Conversation history is kept client-side only — POST /helpdesk/chat is
 * stateless (see apps/api/routers/helpdesk.py). Draggable: the launcher
 * bubble (and the header, once open) can be dragged anywhere on screen; the
 * panel always opens anchored above the bubble.
 */
export function HelpDeskWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<HelpDeskMessage[]>([GREETING]);
  const [draft, setDraft] = useState("");
  const chat = useHelpDeskChat();
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { offset, dragging, onPointerDown, consumeWasDragged } = useDraggableOffset(60);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, chat.isPending]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  function submit(text: string) {
    if (chat.isPending) return;
    const parsed = draftSchema.safeParse(text);
    if (!parsed.success) return;
    const value = parsed.data;

    const history = messages;
    setMessages((prev) => [...prev, { role: "user", content: value }]);
    setDraft("");

    chat.mutate(
      { message: value, history },
      {
        onSuccess: (result) => {
          setMessages((prev) => [...prev, { role: "assistant", content: result.reply }]);
        },
        onError: (err) => {
          setMessages((prev) => [
            ...prev,
            { role: "assistant", content: `Sorry, something went wrong: ${err.message}` },
          ]);
        },
      }
    );
  }

  return (
    <div
      className="fixed z-50 flex flex-col items-end gap-4"
      style={{ right: offset.right, bottom: offset.bottom }}
    >
      {open && (
        <div className="flex h-[38rem] max-h-[calc(100vh-8rem)] w-[23rem] max-w-[calc(100vw-3rem)] flex-col overflow-hidden rounded-3xl border border-slate-200/80 bg-white shadow-2xl ring-1 ring-black/5 dark:border-slate-800 dark:bg-slate-900">
          {/* Header — also a drag handle */}
          <div
            onPointerDown={onPointerDown}
            className="relative flex cursor-grab items-center gap-3 bg-gradient-to-br from-primary-500 via-primary-600 to-primary-700 px-5 py-4 text-white active:cursor-grabbing"
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-white/15 backdrop-blur-sm ring-1 ring-white/20">
              <Sparkles size={18} aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-[15px] font-bold leading-tight">Veerox Help</p>
              <p className="flex items-center gap-1.5 text-[11.5px] text-white/80">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-300" />
                Always available
              </p>
            </div>
            <GripVertical size={15} aria-hidden className="shrink-0 text-white/50" />
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close help chat"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-white/80 transition-colors duration-150 hover:bg-white/15 hover:text-white"
            >
              <X size={17} aria-hidden />
            </button>
          </div>

          {/* Messages */}
          <div
            ref={scrollRef}
            className="flex flex-1 flex-col gap-3.5 overflow-y-auto bg-slate-50/70 px-4 py-4 dark:bg-slate-950/40"
          >
            {messages.map((m, i) => (
              <MessageBubble key={i} message={m} />
            ))}

            {chat.isPending && (
              <div className="flex items-end gap-2">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-sm">
                  <Bot size={14} aria-hidden />
                </div>
                <div className="rounded-2xl rounded-bl-md border border-slate-200/80 bg-white px-3.5 py-2.5 shadow-sm dark:border-slate-700/80 dark:bg-slate-800">
                  <TypingDots />
                </div>
              </div>
            )}

            {messages.length === 1 && !chat.isPending && (
              <div className="mt-1 flex flex-col gap-2">
                <p className="px-1 text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">
                  Try asking
                </p>
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => submit(s)}
                    className="rounded-xl border border-slate-200/80 bg-white px-3.5 py-2.5 text-left text-[13px] font-medium text-slate-600 shadow-sm transition-colors duration-150 hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 dark:border-slate-700/80 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-primary-500/40 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Composer */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(draft);
            }}
            className="flex items-center gap-2 border-t border-slate-200 bg-white p-3.5 dark:border-slate-800 dark:bg-slate-900"
          >
            <input
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Ask about Veerox..."
              className="flex-1 rounded-full border border-slate-200 bg-slate-50 px-4 py-2.5 text-[13.5px] text-slate-800 placeholder:text-slate-400 focus:border-primary-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-primary-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:bg-slate-800 dark:focus:ring-primary-500/20"
            />
            <button
              type="submit"
              disabled={!draft.trim() || chat.isPending}
              aria-label="Send"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-primary-500 to-primary-600 text-white shadow-md transition-transform duration-150 enabled:hover:scale-105 disabled:opacity-40"
            >
              <Send size={16} aria-hidden />
            </button>
          </form>
        </div>
      )}

      <button
        type="button"
        onPointerDown={onPointerDown}
        onClick={() => {
          if (consumeWasDragged()) return;
          setOpen((v) => !v);
        }}
        aria-label={open ? "Close help chat" : "Open help chat"}
        className={`group flex h-15 w-15 touch-none items-center justify-center rounded-full bg-gradient-to-br from-primary-500 to-primary-600 text-white shadow-xl ring-4 ring-primary-500/15 transition-shadow duration-200 hover:shadow-2xl ${
          dragging ? "cursor-grabbing" : "cursor-grab active:scale-95"
        }`}
        style={{ height: "3.75rem", width: "3.75rem" }}
      >
        {open ? (
          <X size={24} aria-hidden />
        ) : (
          <Sparkles size={24} aria-hidden className="transition-transform duration-200 group-hover:scale-110" />
        )}
      </button>
    </div>
  );
}
