"use client";

import { useState } from "react";
import { Brain, Pencil, Plus, Trash2 } from "lucide-react";

import { MetricsPanel } from "@/components/conversation-engine/metrics-panel";
import { ScenarioBuilder } from "@/components/conversation-engine/scenario-builder";
import { Simulator } from "@/components/conversation-engine/simulator";
import {
  AuditSection,
  VersionsSection,
} from "@/components/conversation-engine/versions-audit";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  Card,
  CardContent,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogTitle,
  DialogTrigger,
  EmptyState,
  Input,
  Label,
  Select,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Textarea,
  useConfirm,
  useToast,
} from "@/components/ui";
import {
  useConversationEngineStatus,
  useConversationTurns,
  useCreateKnowledgeItem,
  useDeleteKnowledgeItem,
  useEngineConfig,
  useKnowledgeItems,
  useReviewTurn,
  useSetConversationMode,
  useUpdateKnowledgeItem,
} from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import type {
  ConversationMode,
  ConversationTurn,
  KnowledgeItem,
  KnowledgeKind,
  KnowledgeLanguage,
} from "@/lib/types";

const KINDS: { value: KnowledgeKind; label: string }[] = [
  { value: "product", label: "Product" },
  { value: "pricing", label: "Pricing" },
  { value: "policy", label: "Policy" },
  { value: "faq", label: "FAQ" },
];

const LANGUAGES: { value: "" | KnowledgeLanguage; label: string }[] = [
  { value: "", label: "Any language" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "hinglish", label: "Hinglish" },
];

function KnowledgeDialog({ item }: { item?: KnowledgeItem }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<KnowledgeKind>(item?.kind ?? "faq");
  const [title, setTitle] = useState(item?.title ?? "");
  const [body, setBody] = useState(item?.body ?? "");
  const [keywords, setKeywords] = useState(item?.keywords ?? "");
  const [language, setLanguage] = useState<"" | KnowledgeLanguage>(item?.language ?? "");
  const create = useCreateKnowledgeItem();
  const update = useUpdateKnowledgeItem();
  const { toast } = useToast();
  const pending = create.isPending || update.isPending;

  function reset() {
    setKind(item?.kind ?? "faq");
    setTitle(item?.title ?? "");
    setBody(item?.body ?? "");
    setKeywords(item?.keywords ?? "");
    setLanguage(item?.language ?? "");
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;
    const payload = {
      kind,
      title: title.trim(),
      body: body.trim(),
      keywords: keywords.trim() || null,
      language: language || null,
    };
    const callbacks = {
      onSuccess: () => {
        toast({
          title: item ? "Knowledge updated" : "Knowledge added",
          variant: "success" as const,
        });
        setOpen(false);
      },
      onError: (err: Error) =>
        toast({ title: "Could not save", description: err.message, variant: "error" as const }),
    };
    if (item) update.mutate({ id: item.id, ...payload }, callbacks);
    else create.mutate(payload, callbacks);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) reset();
      }}
    >
      <DialogTrigger>
        {item ? (
          <Button variant="ghost" size="sm" aria-label={`Edit ${item.title}`}>
            <Pencil size={14} aria-hidden />
          </Button>
        ) : (
          <Button variant="primary" size="sm">
            <Plus size={15} aria-hidden />
            Add knowledge
          </Button>
        )}
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>{item ? "Edit knowledge" : "Add approved knowledge"}</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="kn-kind">Type</Label>
              <Select id="kn-kind" value={kind} onChange={(v) => setKind(v as KnowledgeKind)}>
                {KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="kn-title" required>
                Title
              </Label>
              <Input
                id="kn-title"
                required
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Starter plan price"
              />
            </div>
            <div>
              <Label htmlFor="kn-body" required>
                Answer the AI may give
              </Label>
              <Textarea
                id="kn-body"
                rows={4}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder="Exactly what the AI is allowed to say. It will not add anything beyond this."
              />
            </div>
            <div>
              <Label htmlFor="kn-keywords">Trigger words</Label>
              <Input
                id="kn-keywords"
                value={keywords}
                onChange={(e) => setKeywords(e.target.value)}
                placeholder="price, cost, plan, kitna"
              />
              <p className="mt-1 text-xs text-slate-400">
                Words a customer might use. Include Hindi/Hinglish spellings.
              </p>
            </div>
            <div>
              <Label htmlFor="kn-language">Language</Label>
              <Select
                id="kn-language"
                value={language}
                onChange={(v) => setLanguage(v as "" | KnowledgeLanguage)}
              >
                {LANGUAGES.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label}
                  </option>
                ))}
              </Select>
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={pending}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function KnowledgeRow({ item }: { item: KnowledgeItem }) {
  const update = useUpdateKnowledgeItem();
  const remove = useDeleteKnowledgeItem();
  const confirm = useConfirm();
  const { toast } = useToast();

  async function handleDelete() {
    const ok = await confirm({
      title: "Delete this knowledge?",
      description: `"${item.title}" will be removed. The AI will stop answering from it.`,
      confirmLabel: "Delete",
      variant: "danger",
    });
    if (!ok) return;
    remove.mutate(item.id, {
      onError: (err) =>
        toast({ title: "Could not delete", description: err.message, variant: "error" }),
    });
  }

  return (
    <TableRow>
      <TableCell>
        <span className="font-semibold text-slate-800 dark:text-slate-100">{item.title}</span>
        <span className="block max-w-md truncate text-xs text-slate-400">{item.body}</span>
      </TableCell>
      <TableCell>
        <Badge variant="neutral" icon={null}>
          {item.kind}
        </Badge>
      </TableCell>
      <TableCell className="text-xs text-slate-500">{item.language ?? "any"}</TableCell>
      <TableCell>
        <Button
          variant="ghost"
          size="sm"
          aria-label={item.active ? `Disable ${item.title}` : `Enable ${item.title}`}
          onClick={() =>
            update.mutate(
              { id: item.id, active: !item.active },
              {
                onError: (err) =>
                  toast({ title: "Could not update", description: err.message, variant: "error" }),
              },
            )
          }
        >
          <Badge variant={item.active ? "success" : "ended"} icon={null}>
            {item.active ? "Active" : "Off"}
          </Badge>
        </Button>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1">
          <KnowledgeDialog item={item} />
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Delete ${item.title}`}
            onClick={handleDelete}
          >
            <Trash2 size={14} aria-hidden />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function KnowledgeSection() {
  const items = useKnowledgeItems();
  const data = items.data ?? [];
  return (
    <Card>
      <CardContent className="p-0">
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-700">
          <div>
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              Approved knowledge
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              The AI answers factual questions only from these. Anything else is handed to your
              team.
            </p>
          </div>
          <KnowledgeDialog />
        </div>
        <QueryBoundary
          isLoading={items.isLoading}
          isError={items.isError}
          error={items.error}
          isEmpty={data.length === 0}
          onRetry={() => items.refetch()}
          loadingFallback={
            <table className="w-full border-collapse text-sm">
              <tbody>
                <SkeletonRows rows={3} cols={5} />
              </tbody>
            </table>
          }
          emptyFallback={
            <EmptyState
              icon={Brain}
              title="No knowledge yet"
              description="Add your pricing, products and policies so the AI can answer without guessing."
              className="border-0"
            />
          }
        >
          <div className="overflow-x-auto">
            <Table>
              <thead>
                <TableRow isHeader>
                  <TableHeader>Title</TableHeader>
                  <TableHeader>Type</TableHeader>
                  <TableHeader>Language</TableHeader>
                  <TableHeader>Status</TableHeader>
                  <TableHeader>Actions</TableHeader>
                </TableRow>
              </thead>
              <tbody>
                {data.map((item) => (
                  <KnowledgeRow key={item.id} item={item} />
                ))}
              </tbody>
            </Table>
          </div>
        </QueryBoundary>
      </CardContent>
    </Card>
  );
}

function ReviewCell({ turn }: { turn: ConversationTurn }) {
  const review = useReviewTurn();
  const config = useEngineConfig();
  const { toast } = useToast();
  const scenarios = config.data?.base ?? [];
  const onError = (err: Error) =>
    toast({ title: "Could not save review", description: err.message, variant: "error" });

  if (turn.scenario === null || turn.scenario === "silence") {
    return <span className="text-xs text-slate-400">—</span>;
  }
  return (
    <div className="flex min-w-[9rem] flex-col gap-1">
      <div className="flex items-center gap-1">
        <Button
          variant={turn.review_label === "correct" ? "primary" : "outline"}
          size="sm"
          aria-pressed={turn.review_label === "correct"}
          aria-label="Mark understanding as right"
          onClick={() =>
            review.mutate(
              { id: turn.id, label: turn.review_label === "correct" ? null : "correct" },
              { onError },
            )
          }
        >
          Right
        </Button>
        <Button
          variant={turn.review_label === "incorrect" ? "danger" : "outline"}
          size="sm"
          aria-pressed={turn.review_label === "incorrect"}
          aria-label="Mark understanding as wrong"
          onClick={() =>
            review.mutate(
              { id: turn.id, label: turn.review_label === "incorrect" ? null : "incorrect" },
              { onError },
            )
          }
        >
          Wrong
        </Button>
      </div>
      {turn.review_label === "incorrect" && (
        <Select
          aria-label="What it should have been"
          value={turn.review_scenario ?? ""}
          onChange={(value) =>
            review.mutate(
              { id: turn.id, label: "incorrect", correct_scenario: value || null },
              { onError },
            )
          }
        >
          <option value="">Should have been…</option>
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
      )}
    </div>
  );
}

function TurnRow({ turn }: { turn: ConversationTurn }) {
  const fellBack = turn.fallback_reason !== null;
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap text-xs text-slate-500">
        {formatDateTime(turn.created_at)}
      </TableCell>
      <TableCell>
        <Badge variant={turn.mode === "shadow" ? "voice" : "whatsapp"} icon={null}>
          {turn.channel}
          {turn.mode === "shadow" ? " · shadow" : ""}
        </Badge>
      </TableCell>
      <TableCell className="min-w-[12rem] max-w-xs text-sm">{turn.user_text}</TableCell>
      <TableCell className="whitespace-nowrap text-xs">
        <div className="font-medium text-slate-700 dark:text-slate-200">
          {turn.scenario ?? "—"}
        </div>
        <div className="text-slate-400">
          {turn.emotion ?? "—"} · {turn.language ?? "—"} · {turn.source ?? "—"}
        </div>
      </TableCell>
      <TableCell className="text-xs">
        {turn.confidence === null ? "—" : `${Math.round(turn.confidence * 100)}%`}
      </TableCell>
      <TableCell className="text-xs">
        <div>{turn.state ?? "—"}</div>
        <div className="text-slate-400">
          {turn.knowledge_ids?.length
            ? `${turn.knowledge_ids.length} knowledge used`
            : "no knowledge"}
        </div>
      </TableCell>
      <TableCell className="whitespace-nowrap text-xs">{turn.action_id ?? "—"}</TableCell>
      <TableCell className="min-w-[16rem] max-w-sm text-sm">
        {fellBack ? (
          <Badge variant="danger" icon={null}>
            Fallback: {turn.fallback_reason}
          </Badge>
        ) : (
          (turn.reply_text ?? "—")
        )}
      </TableCell>
      <TableCell className="text-xs text-slate-500">
        {turn.latency_ms === null ? "—" : `${turn.latency_ms} ms`}
        {turn.config_version ? <div className="text-slate-400">v{turn.config_version}</div> : null}
      </TableCell>
      <TableCell>
        <ReviewCell turn={turn} />
      </TableCell>
    </TableRow>
  );
}

function TurnLogSection() {
  const [fallbacksOnly, setFallbacksOnly] = useState(false);
  const [mode, setMode] = useState<"" | "live" | "shadow">("");
  const turns = useConversationTurns({ fallbacksOnly, mode: mode || undefined });
  const data = turns.data ?? [];
  return (
    <Card>
      <CardContent className="p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-700">
          <div>
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Turn log</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Every engine decision: what it understood, what it did and what it said. Mark turns
              Right or Wrong to measure accuracy before enabling more customers.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Select
              aria-label="Filter by mode"
              value={mode}
              onChange={(v) => setMode(v as "" | "live" | "shadow")}
            >
              <option value="">All turns</option>
              <option value="live">Live (WhatsApp)</option>
              <option value="shadow">Shadow (voice)</option>
            </Select>
            <Button
              variant={fallbacksOnly ? "primary" : "outline"}
              size="sm"
              onClick={() => setFallbacksOnly((v) => !v)}
              aria-pressed={fallbacksOnly}
            >
              Fallbacks only
            </Button>
          </div>
        </div>
        <QueryBoundary
          isLoading={turns.isLoading}
          isError={turns.isError}
          error={turns.error}
          isEmpty={data.length === 0}
          onRetry={() => turns.refetch()}
          loadingFallback={
            <table className="w-full border-collapse text-sm">
              <tbody>
                <SkeletonRows rows={4} cols={10} />
              </tbody>
            </table>
          }
          emptyFallback={
            <EmptyState
              icon={Brain}
              title="No turns yet"
              description="Turns appear here once an organization is switched to Human Conversation."
              className="border-0"
            />
          }
        >
          <div className="overflow-x-auto">
            <Table>
              <thead>
                <TableRow isHeader>
                  <TableHeader>When</TableHeader>
                  <TableHeader>Channel</TableHeader>
                  <TableHeader>Customer said</TableHeader>
                  <TableHeader>Understood</TableHeader>
                  <TableHeader>Confidence</TableHeader>
                  <TableHeader>State</TableHeader>
                  <TableHeader>Action</TableHeader>
                  <TableHeader>Reply</TableHeader>
                  <TableHeader>Time</TableHeader>
                  <TableHeader>Review</TableHeader>
                </TableRow>
              </thead>
              <tbody>
                {data.map((turn) => (
                  <TurnRow key={turn.id} turn={turn} />
                ))}
              </tbody>
            </Table>
          </div>
        </QueryBoundary>
      </CardContent>
    </Card>
  );
}

type TabId = "overview" | "scenarios" | "simulator" | "versions" | "metrics";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "scenarios", label: "Scenarios" },
  { id: "simulator", label: "Simulator" },
  { id: "versions", label: "Versions & audit" },
  { id: "metrics", label: "Metrics" },
];

const MODE_LABEL: Record<string, string> = {
  strict_script: "Strict Script",
  smart_script: "Smart Script",
  human_conversation: "Human Conversation",
};

const MODE_HELP: Record<ConversationMode, string> = {
  strict_script: "The AI follows your script exactly, as before. The engine is off.",
  smart_script:
    "Your script answers most messages. The AI only acts on callbacks, appointments, hand-offs and opt-outs, and gets scenario guidance for the rest.",
  human_conversation:
    "The AI understands what the customer means and answers from your approved knowledge, falling back to your script when unsure.",
};

function ModeSwitcher({ mode }: { mode: ConversationMode }) {
  const setMode = useSetConversationMode();
  const confirm = useConfirm();
  const { toast } = useToast();

  async function change(next: string) {
    const target = next as ConversationMode;
    if (target === mode) return;
    const ok = await confirm({
      title: `Switch to ${MODE_LABEL[target]}?`,
      description: `${MODE_HELP[target]} It applies to new messages straight away. You can switch back to Strict Script at any time.`,
      confirmLabel: "Switch",
      variant: "primary",
    });
    if (!ok) return;
    setMode.mutate(target, {
      onSuccess: () => toast({ title: `Now on ${MODE_LABEL[target]}`, variant: "success" }),
      onError: (err) => toast({ title: "Could not switch", description: err.message, variant: "error" }),
    });
  }

  return (
    <div className="flex flex-col gap-1 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <Label htmlFor="ce-mode">How should the AI behave for your organization?</Label>
      <Select id="ce-mode" value={mode} onChange={change} disabled={setMode.isPending}>
        <option value="strict_script">Strict Script (default)</option>
        <option value="smart_script">Smart Script</option>
        <option value="human_conversation">Human Conversation</option>
      </Select>
      <p className="text-xs text-slate-500 dark:text-slate-400">{MODE_HELP[mode]}</p>
    </div>
  );
}

export default function ConversationEnginePage() {
  const status = useConversationEngineStatus();
  const [tab, setTab] = useState<TabId>("overview");
  const mode = status.data?.conversation_mode;
  const off = mode === "strict_script";

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <PageHeader
        title="Conversation Engine"
        description="The AI understands what a customer means, remembers context and answers only from knowledge you approve. Define its scenarios, test them, then publish."
        action={
          status.data ? (
            <div className="flex items-center gap-2">
              <Badge variant={off ? "neutral" : "success"} icon={null}>
                {MODE_LABEL[mode ?? "strict_script"] ?? mode}
              </Badge>
              {status.data.active_config_version ? (
                <Badge variant="voice" icon={null}>
                  Scenarios v{status.data.active_config_version}
                </Badge>
              ) : null}
              {status.data.has_draft ? (
                <Badge variant="live" icon={null}>
                  Draft pending
                </Badge>
              ) : null}
            </div>
          ) : undefined
        }
      />
      {status.data && <ModeSwitcher mode={status.data.conversation_mode} />}
      {status.data && off && (
        <p className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
          This organization is on Strict Script, so the engine is off. You can still build and test
          scenarios here. Switch the mode above to go live.
        </p>
      )}
      <nav
        role="tablist"
        aria-label="Conversation Engine sections"
        className="-mb-2 flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`relative shrink-0 px-3 py-2.5 text-sm font-medium transition-colors ${
              tab === t.id
                ? "text-primary-600 dark:text-primary-400"
                : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
            }`}
          >
            {t.label}
            {tab === t.id && (
              <span className="absolute inset-x-3 -bottom-px h-0.5 rounded-full bg-primary-600 dark:bg-primary-500" />
            )}
          </button>
        ))}
      </nav>
      {tab === "overview" && (
        <>
          <KnowledgeSection />
          <TurnLogSection />
        </>
      )}
      {tab === "scenarios" && <ScenarioBuilder />}
      {tab === "simulator" && <Simulator />}
      {tab === "versions" && (
        <>
          <VersionsSection />
          <AuditSection />
        </>
      )}
      {tab === "metrics" && <MetricsPanel />}
    </div>
  );
}
