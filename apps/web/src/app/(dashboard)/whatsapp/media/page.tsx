"use client";

import { useRef, useState } from "react";
import { FileText, Image as ImageIcon, Paperclip, Pencil, Trash2, Upload, Video } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  Card,
  CardContent,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  EmptyState,
  Input,
  Label,
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
  useDeleteWhatsappAsset,
  useUpdateWhatsappAsset,
  useUploadWhatsappAsset,
  useWhatsappAssets,
} from "@/lib/hooks";
import type { WhatsAppAsset } from "@/lib/types";

const MAX_MB = 16;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function TypeBadge({ mediaType }: { mediaType: string }) {
  const Icon = mediaType === "image" ? ImageIcon : mediaType === "video" ? Video : FileText;
  return (
    <Badge variant="neutral" icon={null}>
      <Icon size={12} aria-hidden className="mr-1 inline" />
      {mediaType}
    </Badge>
  );
}

function UploadDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadAsset = useUploadWhatsappAsset();
  const { toast } = useToast();

  function reset() {
    setName("");
    setDescription("");
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !file) return;
    if (file.size > MAX_MB * 1024 * 1024) {
      toast({ title: `File is larger than ${MAX_MB} MB`, variant: "error" });
      return;
    }
    uploadAsset.mutate(
      { name: name.trim(), description: description.trim() || undefined, file },
      {
        onSuccess: () => {
          toast({ title: "File added", variant: "success" });
          reset();
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not upload file", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="primary" size="sm">
          <Upload size={15} aria-hidden />
          Upload file
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Upload a file</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="asset-name" required>
                Name
              </Label>
              <Input
                id="asset-name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Price List 2026, Company Brochure"
              />
            </div>
            <div>
              <Label htmlFor="asset-description">When should the agent send this?</Label>
              <Textarea
                id="asset-description"
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Short note the AI reads — e.g. 'Current pricing for all plans. Send when a contact asks about price or cost.'"
              />
            </div>
            <div>
              <Label htmlFor="asset-file" required>
                File
              </Label>
              <input
                id="asset-file"
                ref={fileInputRef}
                type="file"
                accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,image/*,video/mp4,video/3gpp"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="mt-1 block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-slate-200 dark:text-slate-400 dark:file:bg-slate-800 dark:hover:file:bg-slate-700"
              />
              <p className="mt-1 text-xs text-slate-400">PDF, document, image, or video — up to {MAX_MB} MB.</p>
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={uploadAsset.isPending}>
              Upload
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditDialog({ asset }: { asset: WhatsAppAsset }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(asset.name);
  const [description, setDescription] = useState(asset.description ?? "");
  const updateAsset = useUpdateWhatsappAsset();
  const { toast } = useToast();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    updateAsset.mutate(
      { id: asset.id, name: name.trim(), description: description.trim() || null },
      {
        onSuccess: () => {
          toast({ title: "File updated", variant: "success" });
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not update file", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setName(asset.name);
          setDescription(asset.description ?? "");
        }
      }}
    >
      <DialogTrigger>
        <Button variant="ghost" size="sm" aria-label={`Edit ${asset.name}`}>
          <Pencil size={14} aria-hidden />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Edit file</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="edit-asset-name" required>
                Name
              </Label>
              <Input
                id="edit-asset-name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="edit-asset-description">When should the agent send this?</Label>
              <Textarea
                id="edit-asset-description"
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <p className="text-xs text-slate-400">
              To replace the file itself, delete this entry and upload again.
            </p>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={updateAsset.isPending}>
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AssetRow({ asset }: { asset: WhatsAppAsset }) {
  const deleteAsset = useDeleteWhatsappAsset();
  const confirm = useConfirm();
  const { toast } = useToast();

  async function handleDelete() {
    const confirmed = await confirm({
      title: "Delete this file?",
      description: `"${asset.name}" will be removed. The AI agent will stop offering it.`,
      confirmLabel: "Delete",
      variant: "danger",
    });
    if (!confirmed) return;
    deleteAsset.mutate(asset.id, {
      onError: (err) =>
        toast({ title: "Could not delete file", description: err.message, variant: "error" }),
    });
  }

  return (
    <TableRow>
      <TableCell>
        <span className="font-semibold text-slate-800 dark:text-slate-100">{asset.name}</span>
        <span className="block text-xs text-slate-400">{asset.filename}</span>
      </TableCell>
      <TableCell>
        <TypeBadge mediaType={asset.media_type} />
      </TableCell>
      <TableCell className="text-xs text-slate-500 dark:text-slate-400">
        {formatSize(asset.size_bytes)}
      </TableCell>
      <TableCell className="max-w-md truncate text-xs text-slate-500 dark:text-slate-400">
        {asset.description || "—"}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1">
          <EditDialog asset={asset} />
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Delete ${asset.name}`}
            onClick={handleDelete}
          >
            <Trash2 size={14} aria-hidden />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

export default function WhatsappMediaPage() {
  const assets = useWhatsappAssets();
  const data = assets.data ?? [];

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="WhatsApp Media"
        description="Files the AI agent can send over WhatsApp when a contact asks for them — a price list, brochure, plan, or short video. Give each a clear name and a note on when to send it."
        action={<UploadDialog />}
      />

      <Card>
        <CardContent className="p-0">
          <QueryBoundary
            isLoading={assets.isLoading}
            isError={assets.isError}
            error={assets.error}
            isEmpty={data.length === 0}
            onRetry={() => assets.refetch()}
            loadingFallback={
              <table className="w-full border-collapse text-sm">
                <tbody>
                  <SkeletonRows rows={3} cols={5} />
                </tbody>
              </table>
            }
            emptyFallback={
              <EmptyState
                icon={Paperclip}
                title="No files yet"
                description="Upload a file so the agent can send it to a contact who asks for it."
                className="border-0"
              />
            }
          >
            <div className="overflow-x-auto">
              <Table>
                <thead>
                  <TableRow isHeader>
                    <TableHeader>Name</TableHeader>
                    <TableHeader>Type</TableHeader>
                    <TableHeader>Size</TableHeader>
                    <TableHeader>When to send</TableHeader>
                    <TableHeader>Actions</TableHeader>
                  </TableRow>
                </thead>
                <tbody>
                  {data.map((asset) => (
                    <AssetRow key={asset.id} asset={asset} />
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
