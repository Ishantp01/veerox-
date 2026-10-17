"use client";

import { useState } from "react";

import {
  Badge,
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogTitle,
  Label,
  Select,
  Textarea,
  useToast,
} from "@/components/ui";
import { useCreateKnowledgeItem, useEngineScripts, useStructureScript } from "@/lib/hooks";
import type { KnowledgeSuggestion, ScenarioConfig, ScriptSuggestions } from "@/lib/types";

const SECTION_LABEL: Record<string, string> = {
  opening: "Opening",
  product: "Product",
  pricing: "Pricing",
  qualification: "Qualification",
  objections: "Objections",
  compliance: "Compliance",
  closing: "Closing",
  other: "Other",
};

/**
 * "Suggest from my script" (blueprint section 13). It reads a script and proposes
 * approved knowledge and scenarios. Nothing is saved until the admin ticks items and
 * adds them, and the original script is never edited.
 */
export function ScriptSuggestionsDialog({
  open,
  onClose,
  onAddScenarios,
}: {
  open: boolean;
  onClose: () => void;
  onAddScenarios: (scenarios: ScenarioConfig[]) => void;
}) {
  const scripts = useEngineScripts();
  const structure = useStructureScript();
  const createKnowledge = useCreateKnowledgeItem();
  const { toast } = useToast();

  const [scriptId, setScriptId] = useState("");
  const [pasted, setPasted] = useState("");
  const [result, setResult] = useState<ScriptSuggestions | null>(null);
  const [pickedKnowledge, setPickedKnowledge] = useState<Set<number>>(new Set());
  const [pickedScenarios, setPickedScenarios] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState(false);

  const list = scripts.data ?? [];

  function analyse() {
    structure.mutate(
      scriptId ? { script_id: scriptId } : { text: pasted },
      {
        onSuccess: (data) => {
          setResult(data);
          setPickedKnowledge(new Set(data.knowledge.map((_, i) => i)));
          setPickedScenarios(new Set(data.scenarios.map((_, i) => i)));
        },
        onError: (err) => toast({ title: "Could not analyse", description: err.message, variant: "error" }),
      },
    );
  }

  function toggle(set: Set<number>, index: number, update: (next: Set<number>) => void) {
    const next = new Set(set);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    update(next);
  }

  async function addSelected() {
    if (!result) return;
    setAdding(true);
    try {
      const knowledge: KnowledgeSuggestion[] = result.knowledge.filter((_, i) => pickedKnowledge.has(i));
      for (const item of knowledge) {
        await createKnowledge.mutateAsync({
          kind: item.kind,
          title: item.title,
          body: item.body,
          keywords: item.keywords,
        });
      }
      const scenarios = result.scenarios.filter((_, i) => pickedScenarios.has(i));
      if (scenarios.length) onAddScenarios(scenarios.map((s) => ({ ...s, builtin: false })));
      toast({
        title: "Added",
        description: `${knowledge.length} knowledge item(s) saved. ${scenarios.length} scenario(s) added to your draft: review, Save draft, then Publish.`,
        variant: "success",
      });
      setResult(null);
      onClose();
    } catch (err) {
      toast({ title: "Could not add everything", description: (err as Error).message, variant: "error" });
    } finally {
      setAdding(false);
    }
  }

  const canAnalyse = Boolean(scriptId || pasted.trim());
  const selectedCount = pickedKnowledge.size + pickedScenarios.size;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogTitle>Suggest setup from my script</DialogTitle>
        <DialogBody className="flex max-h-[30rem] flex-col gap-4 overflow-y-auto">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Reads your script and proposes approved knowledge and scenarios. <strong>Your script is never
            changed.</strong> Nothing is saved until you tick items and add them. Facts that contain a number
            not found in your script are left out.
          </p>

          <div>
            <Label htmlFor="ss-script">Script</Label>
            <Select id="ss-script" value={scriptId} onChange={setScriptId}>
              <option value="">Paste text instead…</option>
              {list.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.channel})
                </option>
              ))}
            </Select>
          </div>
          {!scriptId && (
            <div>
              <Label htmlFor="ss-text">Script text</Label>
              <Textarea
                id="ss-text"
                rows={6}
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                placeholder={"OPENING\nHello, this is …\n\nPRICING\nStarter plan …"}
              />
            </div>
          )}
          <div>
            <Button variant="primary" size="sm" onClick={analyse} loading={structure.isPending} disabled={!canAnalyse}>
              Analyse script
            </Button>
          </div>

          {result && (
            <div className="flex flex-col gap-4">
              {result.dropped.length > 0 && (
                <ul className="list-disc rounded-lg border border-amber-200 bg-amber-50 p-3 pl-7 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                  {result.dropped.map((d) => (
                    <li key={d}>{d}</li>
                  ))}
                </ul>
              )}

              <section aria-label="Sections found">
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Sections found ({result.sections.length})
                </h3>
                <ul className="flex flex-wrap gap-1.5">
                  {result.sections.map((s, i) => (
                    <li key={`${s.title}-${i}`}>
                      <Badge variant="neutral" icon={null}>
                        {s.title} · {SECTION_LABEL[s.kind] ?? s.kind}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </section>

              {result.model_used && (
                <>
                  <section aria-label="Suggested knowledge">
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Approved knowledge ({result.knowledge.length})
                    </h3>
                    {result.knowledge.length === 0 ? (
                      <p className="text-sm text-slate-400">Nothing to suggest.</p>
                    ) : (
                      <ul className="flex flex-col gap-2">
                        {result.knowledge.map((k, i) => (
                          <li key={k.title} className="rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                            <label className="flex items-start gap-2 text-sm">
                              <input
                                type="checkbox"
                                className="mt-1"
                                checked={pickedKnowledge.has(i)}
                                onChange={() => toggle(pickedKnowledge, i, setPickedKnowledge)}
                              />
                              <span>
                                <span className="font-medium">{k.title}</span>{" "}
                                <Badge variant="neutral" icon={null}>
                                  {k.kind}
                                </Badge>
                                <span className="block text-xs text-slate-500">{k.body}</span>
                              </span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>

                  <section aria-label="Suggested scenarios">
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Scenarios ({result.scenarios.length})
                    </h3>
                    {result.scenarios.length === 0 ? (
                      <p className="text-sm text-slate-400">Nothing to suggest.</p>
                    ) : (
                      <ul className="flex flex-col gap-2">
                        {result.scenarios.map((s, i) => (
                          <li key={s.id} className="rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                            <label className="flex items-start gap-2 text-sm">
                              <input
                                type="checkbox"
                                className="mt-1"
                                checked={pickedScenarios.has(i)}
                                onChange={() => toggle(pickedScenarios, i, setPickedScenarios)}
                              />
                              <span>
                                <span className="font-medium">{s.name}</span>
                                <span className="block text-xs text-slate-500">{s.goal}</span>
                                <span className="block text-xs text-slate-400">
                                  e.g. {s.examples.slice(0, 3).join(" · ")}
                                </span>
                              </span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                </>
              )}
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            onClick={addSelected}
            loading={adding}
            disabled={!result || !result.model_used || selectedCount === 0}
          >
            Add selected ({selectedCount})
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
