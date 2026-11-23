"use client";

import { useState } from "react";
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
  Input,
  Pagination,
  Select,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useToast,
} from "@/components/ui";
import { NewTemplateDialog } from "@/components/whatsapp/new-template-dialog";
import {
  useClientPagination,
  useDeleteTemplate,
  useOrgNumbers,
  useSyncTemplates,
  useTemplates,
  useUpdateTemplate,
} from "@/lib/hooks";
import type { Template } from "@/lib/types";

const MEDIA_HEADER_TYPES = new Set(["IMAGE", "VIDEO", "DOCUMENT"]);

function DefaultHeaderMediaCell({ template }: { template: Template }) {
  const [value, setValue] = useState(template.header_example ?? "");
  const updateTemplate = useUpdateTemplate();
  const { toast } = useToast();

  if (!MEDIA_HEADER_TYPES.has(template.header_type ?? "")) {
    return <span className="text-xs text-slate-400">—</span>;
  }

  function handleSave() {
    updateTemplate.mutate(
      { id: template.id, header_example: value.trim() },
      {
        onSuccess: () => toast({ title: "Default header media saved" }),
        onError: (err) =>
          toast({ title: "Could not save default media URL", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Default https:// URL"
        className="h-8 min-w-[180px] font-mono text-xs"
      />
      <Button
        variant="outline"
        size="sm"
        disabled={value.trim() === (template.header_example ?? "") || updateTemplate.isPending}
        onClick={handleSave}
      >
        Save
      </Button>
    </div>
  );
}

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
  // Templates live on a WhatsApp Business Account (WABA), and each WhatsApp number belongs to one. With more than one
  // number, pick which number's WABA to look at; the list and "Sync from Meta" then only touch that WABA, and a WABA
  // with no templates shows none. With a single number nothing changes: every template is shown.
  const orgNumbers = useOrgNumbers();
  const whatsappNumbers = (orgNumbers.data?.phone_numbers ?? []).filter((n) => n.provider === "whatsapp");
  const [pickedNumberId, setPickedNumberId] = useState("");
  const scoped = whatsappNumbers.length > 1;
  const selectedNumber = scoped
    ? (whatsappNumbers.find((n) => n.id === pickedNumberId) ??
      whatsappNumbers.find((n) => n.is_default) ??
      whatsappNumbers[0])
    : undefined;
  const templates = useTemplates(
    selectedNumber ? { numberId: selectedNumber.id, active: true } : undefined,
    { enabled: !scoped || selectedNumber !== undefined },
  );
  const pager = useClientPagination(templates.data ?? [], 20);
  const updateTemplate = useUpdateTemplate();
  const deleteTemplate = useDeleteTemplate();
  const syncTemplates = useSyncTemplates();
  const { toast } = useToast();

  function handleSync() {
    syncTemplates.mutate(selectedNumber ? { numberId: selectedNumber.id } : undefined, {
      onSuccess: (result) => {
        const changed = result.created.length + result.updated.length;
        const titleParts: string[] = [];
        if (result.created.length > 0) titleParts.push(`Added ${result.created.length}`);
        if (result.updated.length > 0) titleParts.push(`Updated ${result.updated.length}`);
        if (result.deactivated > 0) titleParts.push(`Switched off ${result.deactivated} no longer on Meta`);
        if (result.total_on_meta === 0) {
          toast({
            title: "No templates on this WhatsApp account",
            description:
              result.deactivated > 0
                ? `${result.deactivated} saved template(s) are no longer on Meta and were switched off.`
                : "Create a template in Meta (or here) for this account to use it.",
          });
          return;
        }
        toast({
          title:
            changed > 0 || result.deactivated > 0
              ? `${titleParts.join(", ")}${changed > 0 ? " template(s) from Meta" : ""}`
              : "Already up to date",
          description:
            changed > 0
              ? [...result.created, ...result.updated].map((t) => t.name).join(", ")
              : `All ${result.total_on_meta} template(s) on Meta already match locally.`,
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
            {scoped && selectedNumber && (
              <Select
                aria-label="WhatsApp number"
                title="Templates belong to the WhatsApp account this number is on"
                value={selectedNumber.id}
                onChange={setPickedNumberId}
                className="w-56"
              >
                {whatsappNumbers.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.phone_number}
                    {n.waba_id ? ` · WABA …${n.waba_id.slice(-4)}` : " · default WABA"}
                    {n.is_default ? " (primary)" : ""}
                  </option>
                ))}
              </Select>
            )}
            <Button
              variant="outline"
              onClick={handleSync}
              loading={syncTemplates.isPending}
            >
              {!syncTemplates.isPending && <RefreshCw size={15} aria-hidden />}
              Sync from Meta
            </Button>
            <NewTemplateDialog numberId={selectedNumber?.id} />
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
                  <SkeletonRows rows={3} cols={8} />
                </tbody>
              </table>
            }
            emptyFallback={
              <EmptyState
                icon={FileText}
                title={scoped ? "No templates on this WhatsApp account" : "No templates yet"}
                description={
                  scoped
                    ? "This number's WhatsApp account has no templates. Sync from Meta, or add one here."
                    : "Add a template to select it from a dropdown on the WhatsApp send form."
                }
                className="border-0"
              />
            }
          >
            <div data-tour="page-table" className="overflow-x-auto">
              <Table>
                <thead>
                  <TableRow isHeader>
                    <TableHeader>Name</TableHeader>
                    <TableHeader>Language</TableHeader>
                    <TableHeader>Category</TableHeader>
                    <TableHeader>Params</TableHeader>
                    <TableHeader>Default header media</TableHeader>
                    <TableHeader>Status</TableHeader>
                    <TableHeader>Active</TableHeader>
                    <TableHeader>Actions</TableHeader>
                  </TableRow>
                </thead>
                <tbody>
                  {pager.pageRows.map((template) => (
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
                        <DefaultHeaderMediaCell template={template} />
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
            <Pagination
              page={pager.page}
              pageSize={pager.pageSize}
              rowCount={pager.rowCount}
              hasNextPage={pager.hasNextPage}
              onPrev={pager.onPrev}
              onNext={pager.onNext}
            />
          </QueryBoundary>
        </CardContent>
      </Card>
    </div>
  );
}
