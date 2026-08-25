"use client";

import { Repeat, Ban, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useConfirm,
  useToast,
} from "@/components/ui";
import { NewFollowUpRuleDialog } from "@/components/automation/new-follow-up-rule-dialog";
import { FollowUpTaskStatusBadge } from "@/components/automation/follow-up-task-status-badge";
import {
  useCancelFollowUpTask,
  useDeleteFollowUpRule,
  useFollowUpRules,
  useFollowUpTasks,
  useUpdateFollowUpRule,
} from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";

export default function FollowUpsPage() {
  const rules = useFollowUpRules();
  const tasks = useFollowUpTasks();
  const updateRule = useUpdateFollowUpRule();
  const deleteRule = useDeleteFollowUpRule();
  const cancelTask = useCancelFollowUpTask();
  const { toast } = useToast();
  const confirm = useConfirm();

  async function handleDeleteRule(id: string, name: string) {
    const ok = await confirm({
      title: "Delete rule",
      description: `Delete rule "${name}"? This can't be undone.`,
    });
    if (!ok) return;
    deleteRule.mutate(id, {
      onSuccess: () => toast({ title: "Rule deleted", variant: "success" }),
      onError: (err) =>
        toast({ title: "Could not delete rule", description: err.message, variant: "error" }),
    });
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Automated Follow-up"
        description="Rules that automatically re-engage leads over WhatsApp, plus every lead's own scheduled follow-up."
        action={<NewFollowUpRuleDialog />}
      />

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Rules</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <QueryBoundary
            isLoading={rules.isLoading}
            isError={rules.isError}
            error={rules.error}
            isEmpty={(rules.data ?? []).length === 0}
            onRetry={() => rules.refetch()}
            loadingFallback={
              <table className="w-full border-collapse text-sm">
                <tbody>
                  <SkeletonRows rows={2} cols={5} />
                </tbody>
              </table>
            }
            emptyFallback={
              <EmptyState
                icon={Repeat}
                title="No rules yet"
                description="Create a rule to automatically follow up on leads sitting in a given status."
                className="border-0"
              />
            }
          >
            <div className="overflow-x-auto">
              <Table>
                <thead>
                  <TableRow isHeader>
                    <TableHeader>Name</TableHeader>
                    <TableHeader>Trigger</TableHeader>
                    <TableHeader>Message</TableHeader>
                    <TableHeader>Active</TableHeader>
                    <TableHeader>Actions</TableHeader>
                  </TableRow>
                </thead>
                <tbody>
                  {(rules.data ?? []).map((rule) => (
                    <TableRow key={rule.id}>
                      <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                        {rule.name}
                      </TableCell>
                      <TableCell className="text-xs text-slate-600 dark:text-slate-400">
                        status = {rule.trigger_config.status ?? "—"}, wait{" "}
                        {rule.trigger_config.delay_hours ?? 0}h
                      </TableCell>
                      <TableCell className="max-w-xs truncate text-xs text-slate-500">
                        {rule.message_template}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => updateRule.mutate({ id: rule.id, active: !rule.active })}
                        >
                          {rule.active ? "Active" : "Paused"}
                        </Button>
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`Delete ${rule.name}`}
                          onClick={() => handleDeleteRule(rule.id, rule.name)}
                        >
                          <Trash2 size={14} aria-hidden />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </tbody>
              </Table>
            </div>
          </QueryBoundary>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Activity</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <QueryBoundary
            isLoading={tasks.isLoading}
            isError={tasks.isError}
            error={tasks.error}
            isEmpty={(tasks.data ?? []).length === 0}
            onRetry={() => tasks.refetch()}
            loadingFallback={
              <table className="w-full border-collapse text-sm">
                <tbody>
                  <SkeletonRows rows={4} cols={4} />
                </tbody>
              </table>
            }
            emptyFallback={
              <EmptyState
                icon={Repeat}
                title="No follow-up tasks yet"
                description="Tasks appear here once a lead's follow-up date arrives or a rule matches."
                className="border-0"
              />
            }
          >
            <div className="overflow-x-auto">
              <Table>
                <thead>
                  <TableRow isHeader>
                    <TableHeader>Scheduled for</TableHeader>
                    <TableHeader>Status</TableHeader>
                    <TableHeader>Sent</TableHeader>
                    <TableHeader>Actions</TableHeader>
                  </TableRow>
                </thead>
                <tbody>
                  {(tasks.data ?? []).map((task) => (
                    <TableRow key={task.id}>
                      <TableCell className="text-xs text-slate-600 dark:text-slate-400">
                        {formatDateTime(task.run_at)}
                      </TableCell>
                      <TableCell>
                        <FollowUpTaskStatusBadge status={task.status} />
                      </TableCell>
                      <TableCell className="text-xs text-slate-500">
                        {task.sent_at ? formatDateTime(task.sent_at) : "—"}
                      </TableCell>
                      <TableCell>
                        {task.status === "pending" && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => cancelTask.mutate(task.id)}
                          >
                            <Ban size={13} aria-hidden /> Cancel
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
    </div>
  );
}
