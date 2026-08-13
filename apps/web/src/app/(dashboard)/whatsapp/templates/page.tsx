"use client";

import { FileText, Trash2 } from "lucide-react";
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
} from "@/components/ui";
import { NewTemplateDialog } from "@/components/whatsapp/new-template-dialog";
import { useDeleteTemplate, useTemplates, useUpdateTemplate } from "@/lib/hooks";

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

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="WhatsApp Templates"
        description="Meta-approved templates, saved once and picked from a dropdown when sending — no more retyping the name, language, or parameters."
        action={<NewTemplateDialog />}
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
                          onClick={() => deleteTemplate.mutate(template.id)}
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
