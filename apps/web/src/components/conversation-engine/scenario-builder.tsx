"use client";

import { useEffect, useMemo, useState } from "react";
import { FileText, Library, Plus, Rocket, Save, Share2, Trash2, Undo2 } from "lucide-react";

import { ScriptSuggestionsDialog } from "@/components/conversation-engine/script-suggestions-dialog";
import { TemplateLibraryDialog } from "@/components/conversation-engine/template-library-dialog";
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
  Input,
  Label,
  Select,
  Textarea,
  useConfirm,
  useToast,
} from "@/components/ui";
import {
  useConversationEngineStatus,
  useCheckEngineConfig,
  useDiscardEngineDraft,
  useEngineConfig,
  usePublishEngineDraft,
  useSaveEngineDraft,
  useUpsertScenarioTemplate,
} from "@/lib/hooks";
import { useAuth } from "@/lib/auth-context";
import type {
  ConfigCheck,
  ConversationMode,
  LowConfidenceAction,
  ScenarioConfig,
} from "@/lib/types";

const LOW_CONFIDENCE_OPTIONS: { value: LowConfidenceAction; label: string; help: string }[] = [
  { value: "script", label: "Hand over to my script (safest)", help: "Your script answers, exactly as before." },
  { value: "clarify", label: "Ask one short clarifying question", help: "The AI asks the customer to say a little more, and does nothing else." },
  { value: "handoff", label: "Pass to a team member", help: "The question is given to a person for follow-up." },
];

const ACTIONS: { id: string; label: string }[] = [
  { id: "request_callback", label: "Schedule a callback" },
  { id: "book_appointment", label: "Book an appointment" },
  { id: "send_whatsapp_message", label: "Send approved details on WhatsApp" },
  { id: "transfer_to_human", label: "Hand over to a team member" },
  { id: "mark_not_interested", label: "Mark as not interested" },
];

const ID_RE = /^[a-z][a-z0-9_]{1,30}$/;

// Mirrors SMART_SCRIPT_ENGINE_SCENARIOS in apps/api/core/conversation/config.py:
// the only scenarios the engine itself acts on in Smart Script mode. For every
// other scenario Smart Script just hands the guidance to the org's script.
const SMART_SCRIPT_ENGINE_IDS = new Set([
  "human_handoff",
  "angry",
  "not_interested",
  "busy_callback",
  "callback_request",
  "appointment_request",
  "whatsapp_info_request",
  "closing",
]);

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[^a-z]+/, "")
    .slice(0, 28);
}

function blankScenario(id: string, name: string): ScenarioConfig {
  return {
    id,
    name,
    situation: "",
    examples: [],
    goal: "",
    should_do: "",
    must_avoid: "",
    actions: ["none"],
    next_scenarios: [],
    enabled: true,
    reply_example: "",
    builtin: false,
  };
}

function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

function ProblemList({ title, items, tone }: { title: string; items: string[]; tone: "error" | "warn" }) {
  if (items.length === 0) return null;
  const palette =
    tone === "error"
      ? "border-red-200 bg-red-50 text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300"
      : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300";
  return (
    <div role="alert" className={`rounded-lg border p-3 text-sm ${palette}`}>
      <p className="font-semibold">{title}</p>
      <ul className="mt-1 list-disc pl-5">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function ScenarioForm({
  scenario,
  all,
  mode,
  isPlatformAdmin,
  onShare,
  onChange,
  onRemove,
}: {
  scenario: ScenarioConfig;
  all: ScenarioConfig[];
  mode: ConversationMode | undefined;
  isPlatformAdmin: boolean;
  onShare: () => void;
  onChange: (next: ScenarioConfig) => void;
  onRemove: () => void;
}) {
  const [examplesText, setExamplesText] = useState(scenario.examples.join("\n"));
  const [showJson, setShowJson] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const builtin = scenario.builtin === true;
  // In Smart Script the engine only acts on a fixed list of built-in scenarios;
  // for all others the org's script writes the reply, so a scenario's action and
  // example reply are never used. Hide them instead of letting them mislead.
  const scriptWritesReply =
    mode === "smart_script" && !(builtin && SMART_SCRIPT_ENGINE_IDS.has(scenario.id));
  const customAction = scenario.actions.find((a) => a !== "none") ?? "none";

  useEffect(() => {
    setExamplesText(scenario.examples.join("\n"));
    setShowJson(false);
    setJsonError(null);
    // Only reset when a different scenario is selected.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenario.id]);

  function patch(partial: Partial<ScenarioConfig>) {
    onChange({ ...scenario, ...partial });
  }

  function toggleAction(id: string) {
    const current = scenario.actions.filter((a) => a !== "none");
    const next = current.includes(id) ? current.filter((a) => a !== id) : [...current, id];
    patch({ actions: next.length ? next : ["none"] });
  }

  function toggleNext(id: string) {
    const has = scenario.next_scenarios.includes(id);
    patch({
      next_scenarios: has
        ? scenario.next_scenarios.filter((n) => n !== id)
        : [...scenario.next_scenarios, id],
    });
  }

  function applyJson() {
    try {
      const parsed = JSON.parse(jsonText) as ScenarioConfig;
      if (parsed.id !== scenario.id) {
        setJsonError("The id cannot be changed here.");
        return;
      }
      onChange({ ...scenario, ...parsed, builtin: scenario.builtin });
      setExamplesText((parsed.examples ?? []).join("\n"));
      setJsonError(null);
    } catch {
      setJsonError("That is not valid JSON.");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-base font-semibold text-slate-800 dark:text-slate-100">
            {scenario.name || "New scenario"}
          </h3>
          <Badge variant="neutral" icon={null}>
            {builtin ? "Built-in" : "Custom"}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={scenario.enabled ? "outline" : "primary"}
            size="sm"
            onClick={() => patch({ enabled: !scenario.enabled })}
          >
            {scenario.enabled ? "Switch off" : "Switch on"}
          </Button>
          {!builtin && isPlatformAdmin && (
            <Button variant="outline" size="sm" onClick={onShare} aria-label="Share with all organizations">
              <Share2 size={14} aria-hidden />
              Share with all organizations
            </Button>
          )}
          {!builtin && (
            <Button variant="danger" size="sm" onClick={onRemove} aria-label="Delete scenario">
              <Trash2 size={14} aria-hidden />
              Delete
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Name" htmlFor="sc-name">
          <Input id="sc-name" value={scenario.name} onChange={(e) => patch({ name: e.target.value })} />
        </Field>
        <Field
          label="Id"
          htmlFor="sc-id"
          hint={builtin ? "Built-in ids are fixed." : "Fixed once the draft is saved."}
        >
          <Input id="sc-id" value={scenario.id} disabled readOnly />
        </Field>
      </div>

      <Field
        label="Situation: when this happens"
        htmlFor="sc-situation"
        hint="Describe the moment in plain words. The AI uses it to recognise the scenario."
      >
        <Textarea
          id="sc-situation"
          rows={2}
          value={scenario.situation}
          onChange={(e) => patch({ situation: e.target.value })}
        />
      </Field>

      <Field
        label="Example phrases (one per line)"
        htmlFor="sc-examples"
        hint="What customers might say, in any language. They guide recognition: they are not exact-match rules, but a phrase that appears in a message selects this scenario."
      >
        <Textarea
          id="sc-examples"
          rows={4}
          value={examplesText}
          onChange={(e) => {
            setExamplesText(e.target.value);
            patch({
              examples: e.target.value
                .split("\n")
                .map((line) => line.trim())
                .filter(Boolean),
            });
          }}
          placeholder={"I am in a meeting\nabhi busy hoon"}
        />
      </Field>

      <Field label="Goal" htmlFor="sc-goal" hint="What the business wants from this moment.">
        <Textarea id="sc-goal" rows={2} value={scenario.goal} onChange={(e) => patch({ goal: e.target.value })} />
      </Field>

      <div className="grid gap-4 md:grid-cols-2">
        <Field label="What the AI should do" htmlFor="sc-do">
          <Textarea
            id="sc-do"
            rows={3}
            value={scenario.should_do}
            onChange={(e) => patch({ should_do: e.target.value })}
          />
        </Field>
        <Field label="What the AI must avoid" htmlFor="sc-avoid">
          <Textarea
            id="sc-avoid"
            rows={3}
            value={scenario.must_avoid}
            onChange={(e) => patch({ must_avoid: e.target.value })}
          />
        </Field>
      </div>

      {scriptWritesReply && (
        <p className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
          Your organization is on <strong>Smart Script</strong>: your own script writes the reply, and
          this scenario only guides it (situation, examples, goal, do and avoid). Actions and example
          replies are hidden because they are not used in this mode. Switch to Human Conversation
          (top of the page) to use them.
        </p>
      )}

      {scriptWritesReply ? null : builtin ? (
        <Field
          label="Actions this scenario may use"
          hint="Unticking an action only removes it: the AI will still reply but will not run it. Nothing outside this list can ever run."
        >
          <div className="flex flex-col gap-1.5 pt-1">
            {ACTIONS.map((action) => (
              <label key={action.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
                <input
                  type="checkbox"
                  checked={scenario.actions.includes(action.id)}
                  onChange={() => toggleAction(action.id)}
                />
                {action.label}
              </label>
            ))}
          </div>
        </Field>
      ) : (
        <Field
          label="Action to run (optional)"
          htmlFor="sc-action"
          hint="The one approved action the backend runs for this scenario. The AI never runs anything itself."
        >
          <Select
            id="sc-action"
            value={customAction}
            onChange={(value) => patch({ actions: value === "none" ? ["none"] : ["none", value] })}
          >
            <option value="none">Just reply (no action)</option>
            {ACTIONS.map((action) => (
              <option key={action.id} value={action.id}>
                {action.label}
              </option>
            ))}
          </Select>
        </Field>
      )}

      <Field
        label="Usually moves next to"
        hint="Where the conversation tends to go afterwards. Passed to the AI as guidance."
      >
        <div className="flex flex-wrap gap-1.5 pt-1">
          {all
            .filter((s) => s.id !== scenario.id)
            .map((s) => {
              const on = scenario.next_scenarios.includes(s.id);
              return (
                <button
                  key={s.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggleNext(s.id)}
                  className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                    on
                      ? "border-primary-500 bg-primary-50 text-primary-700 dark:bg-primary-500/15 dark:text-primary-300"
                      : "border-slate-300 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`}
                >
                  {s.name}
                </button>
              );
            })}
        </div>
      </Field>

      {!scriptWritesReply && (
        <Field
          label="Example reply"
          htmlFor="sc-reply"
          hint="A sample of the wanted tone. For a custom scenario it is also the wording used if the AI model is unavailable."
        >
          <Textarea
            id="sc-reply"
            rows={2}
            value={scenario.reply_example}
            onChange={(e) => patch({ reply_example: e.target.value })}
          />
        </Field>
      )}

      <div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setJsonText(JSON.stringify({ ...scenario, builtin: undefined }, null, 2));
            setShowJson((v) => !v);
          }}
        >
          {showJson ? "Hide JSON" : "Advanced: edit as JSON"}
        </Button>
        {showJson && (
          <div className="mt-2 flex flex-col gap-2">
            <Textarea
              aria-label="Scenario JSON"
              rows={12}
              className="font-mono text-xs"
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
            />
            {jsonError && <p className="text-xs text-red-600">{jsonError}</p>}
            <div>
              <Button size="sm" variant="outline" onClick={applyJson}>
                Apply JSON
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function NewScenarioDialog({
  open,
  onClose,
  existing,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  existing: string[];
  onCreate: (scenario: ScenarioConfig) => void;
}) {
  const [name, setName] = useState("");
  const id = useMemo(() => slugify(name), [name]);
  const taken = existing.includes(id);
  const valid = ID_RE.test(id) && !taken;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setName("");
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogTitle>New scenario</DialogTitle>
        <DialogBody className="flex flex-col gap-3">
          <Field label="Name" htmlFor="new-sc-name" hint="For example: Warranty question, Delivery time.">
            <Input id="new-sc-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </Field>
          <p className="text-xs text-slate-500">
            Id: <code>{id || "…"}</code>
            {taken && <span className="ml-2 text-red-600">already used</span>}
          </p>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!valid}
            onClick={() => {
              onCreate(blankScenario(id, name.trim()));
              setName("");
            }}
          >
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PublishDialog({
  open,
  onClose,
  onPublish,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  onPublish: (note: string) => void;
  pending: boolean;
}) {
  const [note, setNote] = useState("");
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogTitle>Publish this configuration</DialogTitle>
        <DialogBody className="flex flex-col gap-3">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            New conversations will use it straight away. Conversations already under way keep the
            version they started with. You can roll back at any time.
          </p>
          <Field label="Note (optional)" htmlFor="publish-note" hint="What changed and why. Shown in the version history.">
            <Input id="publish-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
          </Field>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={pending} onClick={() => onPublish(note.trim())}>
            Publish
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ScenarioBuilder() {
  const config = useEngineConfig();
  const status = useConversationEngineStatus();
  const check = useCheckEngineConfig();
  const save = useSaveEngineDraft();
  const discard = useDiscardEngineDraft();
  const publish = usePublishEngineDraft();
  const confirm = useConfirm();
  const { toast } = useToast();
  const { user } = useAuth();
  const shareTemplate = useUpsertScenarioTemplate();
  const isPlatformAdmin = Boolean(user?.is_superuser);

  const [lowAction, setLowAction] = useState<LowConfidenceAction>("script");
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [scriptOpen, setScriptOpen] = useState(false);
  const [scenarios, setScenarios] = useState<ScenarioConfig[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [result, setResult] = useState<ConfigCheck | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);

  const data = config.data;
  const draftSource = data?.draft?.scenarios ?? null;
  const base = data?.base ?? null;

  // Load the working copy from the server once, and again after a save,
  // publish or discard (when nothing local is waiting to be saved).
  useEffect(() => {
    if (!data || dirty) return;
    const source = draftSource ?? base;
    if (!source) return;
    setScenarios(source.map((s) => ({ ...s })));
    setLowAction(
      (data.draft?.settings?.low_confidence_action ?? data.base_settings?.low_confidence_action ?? "script") as LowConfidenceAction,
    );
    setSelectedId((current) => current ?? source[0]?.id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, dirty]);

  const selected = scenarios?.find((s) => s.id === selectedId) ?? null;
  const payload = scenarios ? { scenarios, settings: { low_confidence_action: lowAction } } : null;
  const hasDraft = data?.draft != null;
  const activeVersion = data?.active?.version ?? null;

  function update(next: ScenarioConfig) {
    setScenarios((list) => (list ? list.map((s) => (s.id === next.id ? next : s)) : list));
    setDirty(true);
    setResult(null);
  }

  async function runCheck(): Promise<ConfigCheck | null> {
    if (!payload) return null;
    try {
      const outcome = await check.mutateAsync(payload);
      setResult(outcome);
      return outcome;
    } catch (err) {
      toast({ title: "Could not check", description: (err as Error).message, variant: "error" });
      return null;
    }
  }

  async function handleSave(): Promise<boolean> {
    if (!payload) return false;
    const outcome = await runCheck();
    if (!outcome) return false;
    if (!outcome.valid) {
      toast({ title: "Fix the problems first", description: "See the list above the editor.", variant: "error" });
      return false;
    }
    try {
      await save.mutateAsync(payload);
      setDirty(false);
      toast({ title: "Draft saved", variant: "success" });
      return true;
    } catch (err) {
      toast({ title: "Could not save", description: (err as Error).message, variant: "error" });
      return false;
    }
  }

  async function handlePublishClick() {
    if (dirty || !hasDraft) {
      const saved = await handleSave();
      if (!saved) return;
    }
    const outcome = await runCheck();
    if (!outcome) return;
    if (outcome.regression.length > 0) {
      toast({
        title: "Publishing is blocked",
        description: "This change would break paths that already work. See the list above.",
        variant: "error",
      });
      return;
    }
    setPublishOpen(true);
  }

  async function handleDiscard() {
    const ok = await confirm({
      title: "Discard the draft?",
      description: "Your unpublished changes will be lost. The live configuration is not affected.",
      confirmLabel: "Discard",
      variant: "danger",
    });
    if (!ok) return;
    try {
      if (hasDraft) await discard.mutateAsync();
      setDirty(false);
      setResult(null);
      setScenarios(null);
      setSelectedId(null);
      config.refetch();
    } catch (err) {
      toast({ title: "Could not discard", description: (err as Error).message, variant: "error" });
    }
  }

  function addScenarios(incoming: ScenarioConfig[]) {
    setScenarios((list) => {
      const have = new Set((list ?? []).map((s) => s.id));
      const fresh = incoming.filter((s) => !have.has(s.id));
      return [...(list ?? []), ...fresh];
    });
    setSelectedId(incoming[0]?.id ?? null);
    setDirty(true);
    setResult(null);
  }

  async function handleShare(scenario: ScenarioConfig) {
    const ok = await confirm({
      title: `Share "${scenario.name}" with every organization?`,
      description:
        "It is added to the template library. Organizations can copy it into their own draft; their copies stay independent of later edits here.",
      confirmLabel: "Share",
      variant: "primary",
    });
    if (!ok) return;
    shareTemplate.mutate(scenario, {
      onSuccess: () => toast({ title: "Added to the template library", variant: "success" }),
      onError: (err) => toast({ title: "Could not share", description: err.message, variant: "error" }),
    });
  }

  async function handleRemove(id: string) {
    const target = scenarios?.find((s) => s.id === id);
    if (!target || target.builtin !== false) return; // built-in scenarios can only be switched off
    const ok = await confirm({
      title: `Delete "${target.name}"?`,
      description:
        "It is removed from your draft. It stays live for customers until you Save draft and Publish. Conversations already under way keep using the version they started with. You can undo this by discarding the draft or rolling back.",
      confirmLabel: "Delete",
      variant: "danger",
    });
    if (!ok) return;
    setScenarios((list) => (list ? list.filter((s) => s.id !== id) : list));
    setSelectedId(null);
    setDirty(true);
    setResult(null);
    toast({
      title: `"${target.name}" removed from the draft`,
      description: "Click Save draft, then Publish to make it live.",
      variant: "success",
    });
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Scenario Builder</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Define the situations the AI recognises and how it behaves in each. Changes are saved as
              a draft, tested in the Simulator, then published as a new version.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={hasDraft || dirty ? "live" : "neutral"} icon={null}>
              {dirty ? "Unsaved changes" : hasDraft ? "Draft saved" : "No draft"}
            </Badge>
            <Badge variant="success" icon={null}>
              {activeVersion ? `Live: version ${activeVersion}` : "Live: built-in defaults"}
            </Badge>
          </div>
        </div>

        <QueryBoundary
          isLoading={config.isLoading}
          isError={config.isError}
          error={config.error}
          isEmpty={false}
          onRetry={() => config.refetch()}
        >
          {scenarios && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" onClick={() => setNewOpen(true)}>
                  <Plus size={14} aria-hidden />
                  New scenario
                </Button>
                <Button variant="outline" size="sm" onClick={() => setTemplatesOpen(true)}>
                  <Library size={14} aria-hidden />
                  Template library
                </Button>
                <Button variant="outline" size="sm" onClick={() => setScriptOpen(true)}>
                  <FileText size={14} aria-hidden />
                  Suggest from my script
                </Button>
                <Button variant="outline" size="sm" onClick={runCheck} loading={check.isPending}>
                  Check
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleSave}
                  loading={save.isPending}
                  disabled={!dirty}
                >
                  <Save size={14} aria-hidden />
                  Save draft
                </Button>
                <Button variant="primary" size="sm" onClick={handlePublishClick} loading={publish.isPending}>
                  <Rocket size={14} aria-hidden />
                  Publish
                </Button>
                <Button variant="ghost" size="sm" onClick={handleDiscard} disabled={!hasDraft && !dirty}>
                  <Undo2 size={14} aria-hidden />
                  Discard draft
                </Button>
              </div>

              {result && (
                <div className="flex flex-col gap-2">
                  <ProblemList title="Fix these before saving" items={result.problems} tone="error" />
                  <ProblemList
                    title="Publishing is blocked: these existing conversation paths would break"
                    items={result.regression}
                    tone="warn"
                  />
                  {result.valid && result.regression.length === 0 && (
                    <p className="text-sm text-emerald-700 dark:text-emerald-400">
                      No problems found. Test it in the Simulator, then publish.
                    </p>
                  )}
                </div>
              )}

              <div className="flex flex-col gap-1 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                <Label htmlFor="sc-low-confidence">When the AI is not sure what the customer means</Label>
                <Select
                  id="sc-low-confidence"
                  value={lowAction}
                  onChange={(value) => {
                    setLowAction(value as LowConfidenceAction);
                    setDirty(true);
                    setResult(null);
                  }}
                >
                  {LOW_CONFIDENCE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {LOW_CONFIDENCE_OPTIONS.find((o) => o.value === lowAction)?.help} Part of the published
                  version, so it can be rolled back. Smart Script always uses your script.
                </p>
              </div>

              <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
                <nav aria-label="Scenarios" className="flex max-h-[34rem] flex-col gap-1 overflow-y-auto pr-1">
                  {scenarios.map((s) => (
                    <div
                      key={s.id}
                      className={`flex items-center gap-1 rounded-lg border transition-colors ${
                        s.id === selectedId
                          ? "border-primary-500 bg-primary-50 dark:bg-primary-500/10"
                          : "border-transparent hover:bg-slate-50 dark:hover:bg-slate-800"
                      }`}
                    >
                    <button
                      type="button"
                      onClick={() => setSelectedId(s.id)}
                      aria-current={s.id === selectedId ? "true" : undefined}
                      className="flex min-w-0 flex-1 items-center justify-between gap-2 px-3 py-2 text-left text-sm"
                    >
                      <span className={s.enabled ? "" : "text-slate-400 line-through"}>{s.name}</span>
                      <span className="flex shrink-0 items-center gap-1">
                        {s.builtin === false && (
                          <Badge variant="voice" icon={null}>
                            custom
                          </Badge>
                        )}
                        {!s.enabled && (
                          <Badge variant="ended" icon={null}>
                            off
                          </Badge>
                        )}
                      </span>
                    </button>
                    {s.builtin === false && (
                      <button
                        type="button"
                        onClick={() => handleRemove(s.id)}
                        aria-label={`Delete ${s.name}`}
                        title="Delete this scenario"
                        className="mr-1 shrink-0 rounded-md p-1.5 text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 dark:hover:bg-red-500/10"
                      >
                        <Trash2 size={14} aria-hidden />
                      </button>
                    )}
                    </div>
                  ))}
                </nav>
                <div className="min-w-0 rounded-lg border border-slate-200 p-4 dark:border-slate-700">
                  {selected ? (
                    <ScenarioForm
                      key={selected.id}
                      scenario={selected}
                      all={scenarios}
                      mode={status.data?.conversation_mode}
                      isPlatformAdmin={isPlatformAdmin}
                      onShare={() => handleShare(selected)}
                      onChange={update}
                      onRemove={() => handleRemove(selected.id)}
                    />
                  ) : (
                    <p className="text-sm text-slate-500">Select a scenario to edit it.</p>
                  )}
                </div>
              </div>
            </>
          )}
        </QueryBoundary>
      </CardContent>

      <TemplateLibraryDialog
        open={templatesOpen}
        onClose={() => setTemplatesOpen(false)}
        existingIds={scenarios?.map((s) => s.id) ?? []}
        isPlatformAdmin={isPlatformAdmin}
        onAdd={(scenario) => {
          addScenarios([scenario]);
          setTemplatesOpen(false);
          toast({
            title: `"${scenario.name}" added to your draft`,
            description: "Review it, then Save draft and Publish.",
            variant: "success",
          });
        }}
      />
      <ScriptSuggestionsDialog
        open={scriptOpen}
        onClose={() => setScriptOpen(false)}
        onAddScenarios={addScenarios}
      />
      <NewScenarioDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        existing={scenarios?.map((s) => s.id) ?? []}
        onCreate={(scenario) => {
          setScenarios((list) => (list ? [...list, scenario] : [scenario]));
          setSelectedId(scenario.id);
          setDirty(true);
          setResult(null);
          setNewOpen(false);
        }}
      />
      <PublishDialog
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        pending={publish.isPending}
        onPublish={(note) =>
          publish.mutate(
            { note: note || null },
            {
              onSuccess: (published) => {
                setPublishOpen(false);
                setDirty(false);
                setScenarios(null);
                setSelectedId(null);
                toast({ title: `Version ${published.version} is live`, variant: "success" });
              },
              onError: (err) => {
                setPublishOpen(false);
                toast({ title: "Could not publish", description: err.message, variant: "error" });
              },
            },
          )
        }
      />
    </Card>
  );
}
