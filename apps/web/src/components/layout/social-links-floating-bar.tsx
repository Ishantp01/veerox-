"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useSocialLinks } from "@/lib/hooks/useSocialLinks";
import { SOCIAL_META } from "@/lib/social-links";

// Anchored by distance from the bottom-right corner (same convention as
// help-desk-widget.tsx's useDraggableOffset) so the bar keeps opening on the
// right no matter where it's been dragged to.
type Offset = { right: number; bottom: number };

const DEFAULT_OFFSET: Offset = { right: 24, bottom: 24 };
const STORAGE_KEY = "veerox:social-links-widget-pos";
const DRAG_THRESHOLD_PX = 4;
const ICON_SIZE = 44;

function clampOffset(offset: Offset, width: number, height: number): Offset {
  const maxRight = Math.max(8, window.innerWidth - width - 8);
  const maxBottom = Math.max(8, window.innerHeight - height - 8);
  return {
    right: Math.min(Math.max(offset.right, 8), maxRight),
    bottom: Math.min(Math.max(offset.bottom, 8), maxBottom),
  };
}

/** Drag-to-reposition for the whole icon column, mirroring the help-desk
 * widget's draggable offset hook. Position persists across reloads. */
function useDraggableOffset(width: number, height: number) {
  const [offset, setOffset] = useState<Offset>(DEFAULT_OFFSET);
  const dragState = useRef<{ startX: number; startY: number; startOffset: Offset; dragged: boolean } | null>(
    null
  );
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) setOffset(clampOffset(JSON.parse(saved), width, height));
    } catch {
      // ignore malformed/blocked storage
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    function handleResize() {
      setOffset((prev) => clampOffset(prev, width, height));
    }
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [width, height]);

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
            clampOffset(
              { right: state.startOffset.right - dx, bottom: state.startOffset.bottom - dy },
              width,
              height
            )
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
    [offset, width, height]
  );

  // Consumed on click to tell a genuine click apart from the end of a drag.
  const consumeWasDragged = useCallback(() => {
    const was = dragState.current?.dragged ?? false;
    if (dragState.current) dragState.current.dragged = false;
    return was;
  }, []);

  return { offset, dragging, onPointerDown, consumeWasDragged };
}

/**
 * Floating, always-visible, draggable social-media icon bar shown on every
 * dashboard page — right side by default, can be repositioned anywhere on
 * screen (position persists via localStorage). Links are platform-wide and
 * superuser-configurable (Settings → Social Links). Renders nothing until at
 * least one link is configured.
 */
export function SocialLinksFloatingBar() {
  const { data } = useSocialLinks();
  const entries = Object.entries(data?.social_links ?? {}).filter(
    (entry): entry is [string, string] => Boolean(entry[1]) && entry[0] in SOCIAL_META
  );

  const height = entries.length * ICON_SIZE + Math.max(0, entries.length - 1) * 10;
  const { offset, dragging, onPointerDown, consumeWasDragged } = useDraggableOffset(ICON_SIZE, height);

  if (entries.length === 0) return null;

  return (
    <div
      className="fixed z-40 flex flex-col gap-2.5"
      style={{ right: offset.right, bottom: offset.bottom }}
    >
      {entries.map(([key, href]) => {
        const { label, Icon } = SOCIAL_META[key];
        return (
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={label}
            title={label}
            onPointerDown={onPointerDown}
            onClick={(e) => {
              if (consumeWasDragged()) e.preventDefault();
            }}
            className={`flex h-11 w-11 touch-none items-center justify-center rounded-full bg-white text-slate-600 shadow-lg ring-1 ring-slate-200/80 transition-transform duration-150 hover:scale-110 hover:text-primary-600 dark:bg-slate-900 dark:text-slate-300 dark:ring-slate-700/80 dark:hover:text-primary-400 ${
              dragging ? "cursor-grabbing" : "cursor-grab"
            }`}
          >
            <Icon size={19} aria-hidden />
          </a>
        );
      })}
    </div>
  );
}
