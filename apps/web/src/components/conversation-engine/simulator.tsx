"use client";

import { useEffect, useRef, useState } from "react";
import { RotateCcw, Send } from "lucide-react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  Input,
  Label,
  Select,
  useToast,
} from "@/components/ui";
import { useConfigVersions, useEngineConfig, useSimulateTurn } from "@/lib/hooks";
import type { SimulateResult } from "@/lib/types";

interface SimTurn {
  customer: string;
  result: SimulateResult;
}

function Chip({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-xs dark:bg-slate-800">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className="font-medium text-slate-800 dark:text-slate-100">{value || "—"}</span>
    </span>
  );
}

function DebugView({ result }: { result: SimulateResult }) {
  const pct = result.confidence === null ? null : `${Math.round(result.confidence * 100)}%`;
  const memory = Object.entries(result.memory_after);
  return (
    <div
      className="mt-2 flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs dark:border-slate-700 dark:bg-slate-900"
      aria-label="Decision details"
    >
      <div className="flex flex-wrap gap-1.5">
        <Chip label="scenario" value={result.scenario} />
        <Chip label="intent" value={result.intent} />
        <Chip label="emotion" value={result.emotion} />
        <Chip label="language" value={result.language} />
        <Chip label="confidence" value={pct} />
        <Chip label="decided by" value={result.source} />
        <Chip label="state" value={`${result.state_before ?? "new"} → ${result.state_after ?? "—"}`} />
        <Chip label="version" value={result.config_source} />
        <Chip label="time" value={`${result.latency_ms} ms`} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-slate-500">Action:</span>
        {result.action_id && result.action_id !== "none" ? (
          <Badge variant="live" icon={null}>
            {result.action_id} (would run on a real call)
          </Badge>
        ) : (
          <span className="text-slate-700 dark:text-slate-200">none</span>
        )}
        {result.action_args && (
          <code className="rounded bg-slate-100 px-1.5 py-0.5 dark:bg-slate-800">
            {JSON.stringify(result.action_args)}
          </code>
        )}
      </div>
      {result.knowledge.length > 0 && (
        <p>
          <span className="text-slate-500">Approved knowledge used: </span>
          {result.knowledge.map((k) => k.title).join(", ")}
        </p>
      )}
      {result.fallback_reason && (
        <p className="text-amber-700 dark:text-amber-400">
          Handled by: {result.handled_by} (reason: {result.fallback_reason})
        </p>
      )}
      {result.guidance && (
        <p>
          <span className="text-slate-500">Scenario guidance: </span>
          {result.guidance}
        </p>
      )}
      {memory.length > 0 && (
        <p>
          <span className="text-slate-500">Memory after this turn: </span>
          {memory.map(([k, v]) => `${k}: ${v}`).join(" · ")}
        </p>
      )}
    </div>
  );
}

export function Simulator() {
  const versions = useConfigVersions();
  const config = useEngineConfig();
  const simulate = useSimulateTurn();
  const { toast } = useToast();

  const [source, setSource] = useState("draft");
  const [channel, setChannel] = useState<"whatsapp" | "voice">("whatsapp");
  const [useLlm, setUseLlm] = useState(true);
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<SimTurn[]>([]);
  const endRef = useRef<HTMLDivElement | null>(null);

  const last = turns[turns.length - 1]?.result;
  const hasDraft = config.data?.draft != null;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length]);

  function reset() {
    setTurns([]);
    setText("");
  }

  function send(e: React.FormEvent) {
    e.preventDefault();
    const message = text.trim();
    if (!message || simulate.isPending) return;
    const history = turns.flatMap((t) => [
      { role: "user" as const, content: t.customer },
      ...(t.result.reply ? [{ role: "assistant" as const, content: t.result.reply }] : []),
    ]);
    simulate.mutate(
      {
        text: message,
        source,
        state: last?.state_after ?? null,
        memory: last?.memory_after ?? {},
        history: history.slice(-8),
        use_llm: useLlm,
        channel,
      },
      {
        onSuccess: (result) => {
          setTurns((list) => [...list, { customer: message, result }]);
          setText("");
        },
        onError: (err) => toast({ title: "Simulation failed", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 p-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Simulator</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Play the customer. It runs the same engine as a real conversation, against the version you
            choose, but nothing is sent, booked or saved.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <Label htmlFor="sim-source">Configuration</Label>
            <Select id="sim-source" value={source} onChange={(v) => { setSource(v); reset(); }}>
              <option value="draft">{hasDraft ? "Current draft" : "Live version (no draft yet)"}</option>
              <option value="active">Live version</option>
              {(versions.data ?? []).map((v) => (
                <option key={v.id} value={String(v.version)}>
                  Version {v.version}
                  {v.note ? `: ${v.note.slice(0, 30)}` : ""}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="sim-channel">Channel</Label>
            <Select id="sim-channel" value={channel} onChange={(v) => setChannel(v as "whatsapp" | "voice")}>
              <option value="whatsapp">WhatsApp</option>
              <option value="voice">Voice call</option>
            </Select>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 pb-2 text-sm text-slate-700 dark:text-slate-200">
              <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
              Use the AI model (costs tokens)
            </label>
          </div>
        </div>

        <div
          className="flex max-h-[28rem] min-h-[10rem] flex-col gap-4 overflow-y-auto rounded-lg border border-slate-200 p-3 dark:border-slate-700"
          aria-live="polite"
        >
          {turns.length === 0 && (
            <p className="m-auto text-sm text-slate-400">
              Type what a customer might say, for example “abhi busy hoon, kal 5 baje call karna”.
            </p>
          )}
          {turns.map((turn, index) => (
            <div key={index} className="flex flex-col gap-2">
              <div className="self-end rounded-2xl rounded-br-sm bg-primary-600 px-3 py-2 text-sm text-white">
                {turn.customer}
              </div>
              <div className="max-w-[90%] self-start">
                <div className="rounded-2xl rounded-bl-sm bg-slate-100 px-3 py-2 text-sm text-slate-800 dark:bg-slate-800 dark:text-slate-100">
                  {turn.result.reply ?? (
                    <span className="italic text-slate-500">
                      No reply from the engine: the {turn.result.handled_by} would answer.
                    </span>
                  )}
                </div>
                <DebugView result={turn.result} />
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>

        <form onSubmit={send} className="flex items-center gap-2">
          <Input
            aria-label="Customer message"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type as the customer…"
            maxLength={2000}
          />
          <Button type="submit" variant="primary" loading={simulate.isPending} disabled={!text.trim()}>
            <Send size={15} aria-hidden />
            Send
          </Button>
          <Button type="button" variant="outline" onClick={reset} disabled={turns.length === 0}>
            <RotateCcw size={15} aria-hidden />
            Restart
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
