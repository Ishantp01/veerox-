"use client";

import { FileText, RefreshCw, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  type BadgeVariant,
  Button,
  Card,
  CardContent,
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useToast,
} from "@/components/ui";
import { NewTemplateDialog } from "@/components/whatsapp/new-template-dialog";
import { useDeleteTemplate, useSyncTemplates, useTemplates, useUpdateTemplate } from "@/lib/hooks";

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  APPROVED: "success",
  PENDING: "live",
  REJECTED: "danger",
};

function StatusBadge({ status }: { status: string | null }) {
  if (!status) {
    return (
      <Badge variant="neutral" icon={null}>
        Not on Meta
      </Badge>
    );
  }
  return (
    <Badge variant={STATUS_VARIANT[status] ?? "neutral"} icon={null}>
      {status.charAt(0) + status.slice(1).toLowerCase()}
    </Badge>
  );
}

export default function TemplatesPage() {
  const templates = useTemplates();
  const updateTemplate = useUpdateTemplate();
  const deleteTemplate = useDeleteTemplate();
  const syncTemplates = useSyncTemplates();
  const { toast } = useToast();

  function handleSync() {
    syncTemplates.mutate(undefined, {
      onSuccess: (result) => {
        toast({
          title:
            result.created.length > 0
              ? `Added ${result.created.length} template(s) from Meta`
              : "Already up to date",
          description:
            result.created.length > 0
              ? result.created.map((t) => t.name).join(", ")
              : `All ${result.total_on_meta} template(s) on Meta already have a local row.`,
        });
      },
      onError: (err) =>
        toast({ title: "Could not sync from Meta", description: err.message, variant: "error" }),
    });
  }

  function handleDelete(id: string, name: string) {
    deleteTemplate.mutate(id, {
      onSuccess: () =>
        toast({
          title: "Removed from this list",
          description: `Still exists on Meta — Sync from Meta will bring "${name}" back.`,
        }),
      onError: (err) =>
        toast({ title: "Could not delete template", description: err.message, variant: "error" }),
    });
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="WhatsApp Templates"
        description="Meta-approved templates, saved once and picked from a dropdown when sending — no more retyping the name, language, or parameters."
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={handleSync}
              loading={syncTemplates.isPending}
            >
              {!syncTemplates.isPending && <RefreshCw size={15} aria-hidden />}
              Sync from Meta
            </Button>
            <NewTemplateDialog />
          </div>
        }
      />

      <Card>
        <CardContent className="p-0">
          <QueryBoundary
            isLoading={templates.isLoading}
            isError={templates.isError}
            error={templates.error}
            isEmpty={(templates.data ?? []).length === 0}
            onRetry={() => templates.refetch()}
            loadingFallback={
              <table className="w-full border-collapse text-sm">
                <tbody>
                  <SkeletonRows rows={3} cols={7} />
                </tbody>
              </table>
            }
            emptyFallback={
              <EmptyState
                icon={FileText}
                title="No templates yet"
                description="Add a template to select it from a dropdown on the WhatsApp send form."
                className="border-0"
              />
            }
          >
            <div className="overflow-x-auto">
              <Table>
                <thead>
                  <TableRow isHeader>
                    <TableHeader>Name</TableHeader>
                    <TableHeader>Language</TableHeader>
                    <TableHeader>Category</TableHeader>
                    <TableHeader>Params</TableHeader>
                    <TableHeader>Status</TableHeader>
                    <TableHeader>Active</TableHeader>
                    <TableHeader>Actions</TableHeader>
                  </TableRow>
                </thead>
                <tbody>
                  {(templates.data ?? []).map((template) => (
                    <TableRow key={template.id}>
                      <TableCell className="font-mono text-xs font-semibold text-slate-800 dark:text-slate-100">
                        {template.name}
                      </TableCell>
                      <TableCell className="text-xs text-slate-600 dark:text-slate-400">
                        {template.language}
                      </TableCell>
                      <TableCell className="text-xs text-slate-500">
                        {template.category ?? "—"}
                      </TableCell>
                      <TableCell className="text-xs text-slate-500">
                        {template.param_labels.length === 0
                          ? "None"
                          : template.param_labels.join(", ")}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={template.meta_status} />
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            updateTemplate.mutate({ id: template.id, active: !template.active })
                          }
                        >
                          {template.active ? "Active" : "Inactive"}
                        </Button>
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`Delete ${template.name}`}
                          onClick={() => handleDelete(template.id, template.name)}
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
    </div>
  );
}
