"use client";

import { Library, Trash2 } from "lucide-react";

import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogTitle,
  EmptyState,
  useConfirm,
  useToast,
} from "@/components/ui";
import { useDeleteScenarioTemplate, useScenarioTemplates } from "@/lib/hooks";
import type { ScenarioConfig } from "@/lib/types";

/**
 * The platform's scenario template library (blueprint section 12). Any org admin can
 * copy a template into their own draft; the copy is independent, so a later change to
 * the template never alters a scenario the org already has. Only platform admins see
 * the delete button.
 */
export function TemplateLibraryDialog({
  open,
  onClose,
  existingIds,
  isPlatformAdmin,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  existingIds: string[];
  isPlatformAdmin: boolean;
  onAdd: (scenario: ScenarioConfig) => void;
}) {
  const templates = useScenarioTemplates();
  const remove = useDeleteScenarioTemplate();
  const confirm = useConfirm();
  const { toast } = useToast();
  const data = templates.data ?? [];

  async function handleDelete(id: string, name: string) {
    const ok = await confirm({
      title: `Remove "${name}" from the library?`,
      description:
        "Organizations that already copied it keep their copy. It just stops being offered to others.",
      confirmLabel: "Remove",
      variant: "danger",
    });
    if (!ok) return;
    remove.mutate(id, {
      onError: (err) => toast({ title: "Could not remove", description: err.message, variant: "error" }),
    });
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogTitle>Scenario template library</DialogTitle>
        <DialogBody className="flex max-h-[26rem] flex-col gap-3 overflow-y-auto">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Ready-made scenarios shared by the platform. Adding one copies it into your draft, where you
            can edit it. Later changes to the template never change your copy.
          </p>
          <QueryBoundary
            isLoading={templates.isLoading}
            isError={templates.isError}
            error={templates.error}
            isEmpty={data.length === 0}
            onRetry={() => templates.refetch()}
            emptyFallback={
              <EmptyState
                icon={Library}
                title="No templates yet"
                description="Platform admins can share a scenario with every organization from the editor."
                className="border-0"
              />
            }
          >
            <ul className="flex flex-col gap-2">
              {data.map((t) => {
                const have = existingIds.includes(t.scenario_id);
                return (
                  <li
                    key={t.scenario_id}
                    className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                        {t.definition.name}
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {t.definition.situation || t.definition.goal}
                      </p>
                      <p className="mt-1 text-xs text-slate-400">
                        {t.definition.examples.length} example phrase
                        {t.definition.examples.length === 1 ? "" : "s"}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      {have ? (
                        <Badge variant="ended" icon={null}>
                          already added
                        </Badge>
                      ) : (
                        <Button
                          size="sm"
                          variant="primary"
                          onClick={() => onAdd({ ...t.definition, builtin: false })}
                        >
                          Add to my draft
                        </Button>
                      )}
                      {isPlatformAdmin && (
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Remove ${t.definition.name} from the library`}
                          onClick={() => handleDelete(t.scenario_id, t.definition.name)}
                        >
                          <Trash2 size={14} aria-hidden />
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </QueryBoundary>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
