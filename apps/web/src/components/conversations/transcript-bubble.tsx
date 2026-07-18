import { Mic, type LucideIcon } from "lucide-react";
import type { Message } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface TranscriptBubbleProps {
  role: Message["role"];
  content: string;
  timestamp: string;
  /** When true, the message arrived over voice — shows a small mic affordance. */
  isVoice?: boolean;
}

interface RoleStyle {
  /** Column alignment of the whole bubble group. */
  wrapper: string;
  /** Bubble background + text. */
  bubble: string;
  label: string;
}

/**
 * Role → visual language. user = indigo (right-aligned), assistant = neutral
 * slate, tool = amber/mono (a function call). Color is paired with a text label
 * so it's never the only signal (a11y §10).
 */
const ROLE_STYLES: Record<Message["role"], RoleStyle> = {
  user: {
    wrapper: "items-end",
    bubble: "bg-gradient-to-br from-primary-500 to-primary-600 text-white shadow-sm",
    label: "User",
  },
  assistant: {
    wrapper: "items-start",
    bubble: "border border-slate-200 bg-slate-50 text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
    label: "Assistant",
  },
  tool: {
    wrapper: "items-start",
    bubble: "border border-amber-200 bg-amber-50 font-mono text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300",
    label: "Tool",
  },
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * Single transcript message rendered as a chat bubble (UI plan §7.2).
 * Content is rendered as plain text (never `dangerouslySetInnerHTML`) per the
 * transcript-injection mitigation (security §11).
 */
export function TranscriptBubble({
  role,
  content,
  timestamp,
  isVoice = false,
}: TranscriptBubbleProps) {
  const styles = ROLE_STYLES[role];
  const MicIcon: LucideIcon = Mic;

  return (
    <div className={cn("flex flex-col gap-1", styles.wrapper)}>
      <span className="flex items-center gap-1.5 px-1 text-xs text-slate-400 dark:text-slate-500">
        {isVoice && <MicIcon size={11} aria-label="Voice message" className="shrink-0" />}
        <span className="font-medium">{styles.label}</span>
        <span aria-hidden>&middot;</span>
        <time dateTime={timestamp}>{formatTime(timestamp)}</time>
      </span>
      <div
        className={cn(
          "max-w-prose whitespace-pre-wrap break-words rounded-2xl px-4 py-3 text-sm leading-relaxed",
          styles.bubble,
        )}
      >
        {content}
      </div>
    </div>
  );
}

export default TranscriptBubble;
