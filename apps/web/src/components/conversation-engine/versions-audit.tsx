"use client";

import { History, RotateCcw } from "lucide-react";

import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useConfirm,
  useToast,
} from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { useConfigVersions, useEngineAudit, useRollbackEngineConfig } from "@/lib/hooks";
import type { EngineAuditEvent } from "@/lib/types";

const ACTION_LABELS: Record<string, string> = {
  "config.draft_created": "Started a draft",
  "config.draft_saved": "Saved the draft",
  "config.draft_discarded": "Discarded the draft",
  "config.published": "Published a version",
  "config.rolled_back": "Rolled back",
  "knowledge.created": "Added knowledge",
  "knowledge.updated": "Edited knowledge",
  "knowledge.deleted": "Deleted knowledge",
  "org.conversation_mode_changed": "Changed AI mode",
  "org.conversation_voice_mode_changed": "Changed voice mode",
};

function describe(event: EngineAuditEvent): string {
  const detail = event.detail ?? {};
  const parts: string[] = [];
  if (typeof detail.version === "number") parts.push(`version ${detail.version}`);
  if (typeof detail.from_version === "number") {
    parts.push(`copied from version ${detail.from_version} as version ${detail.new_version}`);
  }
  if (typeof detail.from === "string" && typeof detail.to === "string") {
    parts.push(`${detail.from} → ${detail.to}`);
  }
  if (typeof detail.title === "string") parts.push(`“${detail.title}”`);
  if (typeof detail.note === "string" && detail.note) parts.push(`note: ${detail.note}`);
  return parts.join(" · ");
}

export function VersionsSection() {
  const versions = useConfigVersions();
  const rollback = useRollbackEngineConfig();
  const confirm = useConfirm();
  const { toast } = useToast();
  const data = versions.data ?? [];
  const activeVersion = data[0]?.version ?? null;

  async function handleRollback(version: number) {
    const ok = await confirm({
      title: `Roll back to version ${version}?`,
      description:
        "A copy of that version is published as a new version, so nothing is lost. New conversations use it straight away; conversations under way keep their version.",
      confirmLabel: "Roll back",
      variant: "danger",
    });
    if (!ok) return;
    rollback.mutate(
      { version },
      {
        onSuccess: (row) => toast({ title: `Rolled back: version ${row.version} is live`, variant: "success" }),
        onError: (err) => toast({ title: "Could not roll back", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <div className="border-b border-slate-200 p-4 dark:border-slate-700">
          <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Published versions</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Every published version is kept exactly as it was. The newest one is live.
          </p>
        </div>
        <QueryBoundary
          isLoading={versions.isLoading}
          isError={versions.isError}
          error={versions.error}
          isEmpty={data.length === 0}
          onRetry={() => versions.refetch()}
          loadingFallback={
            <table className="w-full border-collapse text-sm">
              <tbody>
                <SkeletonRows rows={3} cols={5} />
              </tbody>
            </table>
          }
          emptyFallback={
            <EmptyState
              icon={History}
              title="Nothing published yet"
              description="The built-in scenarios are in use. Publish a configuration from the Scenarios tab to create version 1."
              className="border-0"
            />
          }
        >
          <div className="overflow-x-auto">
            <Table>
              <thead>
                <TableRow isHeader>
                  <TableHeader>Version</TableHeader>
                  <TableHeader>Published</TableHeader>
                  <TableHeader>By</TableHeader>
                  <TableHeader>Note</TableHeader>
                  <TableHeader>Actions</TableHeader>
                </TableRow>
              </thead>
              <tbody>
                {data.map((v) => (
                  <TableRow key={v.id}>
                    <TableCell className="whitespace-nowrap font-semibold">
                      v{v.version}{" "}
                      {v.version === activeVersion && (
                        <Badge variant="success" icon={null}>
                          live
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-slate-500">
                      {v.published_at ? formatDateTime(v.published_at) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">{v.published_by ?? "—"}</TableCell>
                    <TableCell className="max-w-xs text-xs text-slate-600 dark:text-slate-300">
                      {v.rolled_back_from ? `Rollback to v${v.rolled_back_from}. ` : ""}
                      {v.note ?? ""}
                    </TableCell>
                    <TableCell>
                      {v.version !== activeVersion && v.version !== null && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleRollback(v.version as number)}
                          loading={rollback.isPending}
                        >
                          <RotateCcw size={13} aria-hidden />
                          Roll back to this
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </tbody>
            </Table>
          </div>
        </QueryBoundary>
      </CardContent>
    </Card>
  );
}

export function AuditSection() {
  const audit = useEngineAudit();
  const data = audit.data ?? [];
  return (
    <Card>
      <CardContent className="p-0">
        <div className="border-b border-slate-200 p-4 dark:border-slate-700">
          <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Audit log</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Who changed the scenarios and approved knowledge, and when.
          </p>
        </div>
        <QueryBoundary
          isLoading={audit.isLoading}
          isError={audit.isError}
          error={audit.error}
          isEmpty={data.length === 0}
          onRetry={() => audit.refetch()}
          loadingFallback={
            <table className="w-full border-collapse text-sm">
              <tbody>
                <SkeletonRows rows={3} cols={4} />
              </tbody>
            </table>
          }
          emptyFallback={
            <EmptyState
              icon={History}
              title="No changes recorded yet"
              description="Edits to scenarios and knowledge will appear here."
              className="border-0"
            />
          }
        >
          <div className="overflow-x-auto">
            <Table>
              <thead>
                <TableRow isHeader>
                  <TableHeader>When</TableHeader>
                  <TableHeader>Who</TableHeader>
                  <TableHeader>What</TableHeader>
                  <TableHeader>Details</TableHeader>
                </TableRow>
              </thead>
              <tbody>
                {data.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell className="whitespace-nowrap text-xs text-slate-500">
                      {formatDateTime(event.created_at)}
                    </TableCell>
                    <TableCell className="text-xs">{event.actor}</TableCell>
                    <TableCell className="whitespace-nowrap text-sm">
                      {ACTION_LABELS[event.action] ?? event.action}
                    </TableCell>
                    <TableCell className="text-xs text-slate-600 dark:text-slate-300">
                      {describe(event)}
                    </TableCell>
                  </TableRow>
                ))}
              </tbody>
            </Table>
          </div>
        </QueryBoundary>
      </CardContent>
    </Card>
  );
}
