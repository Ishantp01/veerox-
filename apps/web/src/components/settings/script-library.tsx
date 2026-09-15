"use client";

import { useState } from "react";
import { Bot, Pencil, Plus, Star, Trash2 } from "lucide-react";

import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
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
  useCreateScript,
  useDeleteScript,
  useOrgNumbers,
  useScripts,
  useSetDefaultScript,
  useUpdateScriptLibraryItem,
  type ScriptChannel,
} from "@/lib/hooks";
import type { ScriptLibraryItem } from "@/lib/types";

/** This script's paired number, e.g. "Send from" — settings-page label only, see ScriptLibraryItem. */
function PhoneNumberField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const orgNumbers = useOrgNumbers();
  const whatsappNumbers = (orgNumbers.data?.phone_numbers ?? []).filter(
    (n) => n.provider === "whatsapp",
  );
  if (whatsappNumbers.length === 0) return null;
  return (
    <div>
      <Label htmlFor="script-phone-number">Send from (optional)</Label>
      <Select id="script-phone-number" value={value} onChange={onChange} className="w-full">
        <option value="">No number assigned</option>
        {whatsappNumbers.map((n) => (
          <option key={n.id} value={n.id}>
            {n.phone_number}
            {n.is_default ? ", default" : ""}
          </option>
        ))}
      </Select>
      <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
        Messages that arrive on this number use this script as the reply, unless a campaign
        pins a different one. Doesn&apos;t change which number a campaign sends FROM (still
        chosen per campaign).
      </p>
    </div>
  );
}

function ScriptFormFields({
  name,
  onNameChange,
  content,
  onContentChange,
}: {
  name: string;
  onNameChange: (v: string) => void;
  content: string;
  onContentChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Label htmlFor="script-name" required>
          Script name
        </Label>
        <Input
          id="script-name"
          required
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="e.g. July promo, Renewal outreach"
        />
      </div>
      <div>
        <Label htmlFor="script-content" required>
          Script
        </Label>
        <Textarea
          id="script-content"
          required
          rows={14}
          className="font-mono text-xs"
          value={content}
          onChange={(e) => onContentChange(e.target.value)}
          placeholder="What the AI agent says and the flow it follows on calls using this script."
        />
      </div>
    </div>
  );
}

function NewScriptDialog({ channel }: { channel: ScriptChannel }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [makeDefault, setMakeDefault] = useState(false);
  const [phoneNumberId, setPhoneNumberId] = useState("");
  const createScript = useCreateScript();
  const { toast } = useToast();

  function reset() {
    setName("");
    setContent("");
    setMakeDefault(false);
    setPhoneNumberId("");
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !content.trim()) return;
    createScript.mutate(
      {
        name: name.trim(),
        content,
        channel,
        is_default: makeDefault,
        phone_number_id: channel === "whatsapp" ? phoneNumberId || null : null,
      },
      {
        onSuccess: () => {
          toast({ title: "Script added", variant: "success" });
          reset();
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not add script", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="primary" size="sm">
          <Plus size={15} aria-hidden />
          New script
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>New {channel === "whatsapp" ? "WhatsApp" : "calling"} script</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <ScriptFormFields
              name={name}
              onNameChange={setName}
              content={content}
              onContentChange={setContent}
            />
            {channel === "whatsapp" && (
              <PhoneNumberField value={phoneNumberId} onChange={setPhoneNumberId} />
            )}
            <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
              <input
                type="checkbox"
                checked={makeDefault}
                onChange={(e) => setMakeDefault(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-primary-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-700"
              />
              Set as default — used whenever a campaign doesn&apos;t pick a script of its own
            </label>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={createScript.isPending}>
              Add script
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditScriptDialog({ script }: { script: ScriptLibraryItem }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(script.name);
  const [content, setContent] = useState(script.content);
  const [phoneNumberId, setPhoneNumberId] = useState(script.phone_number_id ?? "");
  const updateScript = useUpdateScriptLibraryItem();
  const { toast } = useToast();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !content.trim()) return;
    updateScript.mutate(
      {
        id: script.id,
        name: name.trim(),
        content,
        phone_number_id: script.channel === "whatsapp" ? phoneNumberId || null : undefined,
      },
      {
        onSuccess: () => {
          toast({ title: "Script updated", variant: "success" });
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not update script", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setName(script.name);
          setContent(script.content);
          setPhoneNumberId(script.phone_number_id ?? "");
        }
      }}
    >
      <DialogTrigger>
        <Button variant="ghost" size="sm" aria-label={`Edit ${script.name}`}>
          <Pencil size={14} aria-hidden />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Edit script</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <ScriptFormFields
              name={name}
              onNameChange={setName}
              content={content}
              onContentChange={setContent}
            />
            {script.channel === "whatsapp" && (
              <PhoneNumberField value={phoneNumberId} onChange={setPhoneNumberId} />
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={updateScript.isPending}>
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ScriptRow({ script }: { script: ScriptLibraryItem }) {
  const setDefaultScript = useSetDefaultScript();
  const deleteScript = useDeleteScript();
  const confirm = useConfirm();
  const { toast } = useToast();
  const orgNumbers = useOrgNumbers();
  const assignedNumber = (orgNumbers.data?.phone_numbers ?? []).find(
    (n) => n.id === script.phone_number_id,
  );

  async function handleDelete() {
    const confirmed = await confirm({
      title: "Delete this script?",
      description: `"${script.name}" will be removed from the library. Campaigns using it fall back to the org default at their next call/send.`,
      confirmLabel: "Delete",
      variant: "danger",
    });
    if (!confirmed) return;
    deleteScript.mutate(
      { id: script.id, channel: script.channel },
      {
        onError: (err) =>
          toast({ title: "Could not delete script", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <TableRow>
      <TableCell>
        <span className="font-semibold text-slate-800 dark:text-slate-100">{script.name}</span>
      </TableCell>
      <TableCell className="max-w-md truncate text-xs text-slate-500 dark:text-slate-400">
        {script.content}
      </TableCell>
      {script.channel === "whatsapp" && (
        <TableCell className="text-xs text-slate-500 dark:text-slate-400">
          {assignedNumber?.phone_number ?? "—"}
        </TableCell>
      )}
      <TableCell>
        {script.is_default ? (
          <Badge variant="success" icon={null}>
            Default
          </Badge>
        ) : (
          <Button
            variant="outline"
            size="sm"
            loading={setDefaultScript.isPending}
            onClick={() =>
              setDefaultScript.mutate(script.id, {
                onError: (err) =>
                  toast({
                    title: "Could not set default script",
                    description: err.message,
                    variant: "error",
                  }),
              })
            }
          >
            <Star size={13} aria-hidden /> Set default
          </Button>
        )}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1">
          <EditScriptDialog script={script} />
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Delete ${script.name}`}
            onClick={handleDelete}
          >
            <Trash2 size={14} aria-hidden />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

/**
 * Per-channel AI script library — replaces the single shared ScriptEditor
 * textarea previously used on both the calling and WhatsApp settings tabs.
 * Multiple named scripts can be picked per campaign (see campaigns-view.tsx);
 * the one marked default is the fallback every campaign without its own
 * pick uses, for that channel.
 */
export function ScriptLibrary({ channel = "voice" }: { channel?: ScriptChannel }) {
  const scripts = useScripts(channel);
  const data = scripts.data ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="min-w-[240px] flex-1 text-sm text-slate-500 dark:text-slate-400">
          Create as many scripts as you need — pick one per campaign, or leave the default in
          place as the fallback.
        </p>
        <div className="shrink-0">
          <NewScriptDialog channel={channel} />
        </div>
      </div>
      <QueryBoundary
        isLoading={scripts.isLoading}
        isError={scripts.isError}
        error={scripts.error}
        isEmpty={data.length === 0}
        onRetry={() => scripts.refetch()}
        loadingFallback={
          <table className="w-full border-collapse text-sm">
            <tbody>
              <SkeletonRows rows={2} cols={4} />
            </tbody>
          </table>
        }
        emptyFallback={
          <EmptyState
            icon={Bot}
            title="No scripts yet"
            description="Add a script to select it from a dropdown on the campaign creation form."
            className="border-0"
          />
        }
      >
        <div className="overflow-x-auto rounded-xl border border-slate-200/80 dark:border-slate-800">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Name</TableHeader>
                <TableHeader>Preview</TableHeader>
                {channel === "whatsapp" && <TableHeader>Number</TableHeader>}
                <TableHeader>Default</TableHeader>
                <TableHeader>Actions</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {data.map((script) => (
                <ScriptRow key={script.id} script={script} />
              ))}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>
    </div>
  );
}
