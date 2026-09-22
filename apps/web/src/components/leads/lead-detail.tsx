"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, CalendarClock, Save, Tag, Trash2, UserCircle } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import { IntentBadge } from "@/components/leads/intent-badge";
import { LEAD_STATUS_LABELS, LEAD_STATUS_OPTIONS } from "@/components/leads/status-badge";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogTitle,
  Input,
  Label,
  Select,
  Skeleton,
  Textarea,
  useConfirm,
  useToast,
} from "@/components/ui";
import {
  useCreateLeadStatusPreset,
  useDeleteLead,
  useDeleteLeadStatusPreset,
  useLead,
  useLeadStatusPresets,
  useUpdateLead,
} from "@/lib/hooks";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { LeadStatus } from "@/lib/types";

/** ISO datetime -> value an <input type="datetime-local"> accepts. */
function toDatetimeLocal(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export interface LeadDetailProps {
  id: string;
  /** Where the "back" link goes, e.g. "/whatsapp/leads". */
  backHref: string;
  backLabel: string;
}

/**
 * Lead detail / CRM view — status + follow-up editor plus the lead's full
 * conversation history (joined server-side via user_id). Shared by the
 * per-channel /whatsapp/leads/[id] and /calling/leads/[id] pages.
 */
export function LeadDetail({ id, backHref, backLabel }: LeadDetailProps) {
  const router = useRouter();
  const { toast } = useToast();
  const confirm = useConfirm();
  const lead = useLead(id);
  const updateLead = useUpdateLead();
  const deleteLead = useDeleteLead();
  const { data: statusPresetsData } = useLeadStatusPresets();
  const statusPresets = statusPresetsData ?? [];
  const createStatusPreset = useCreateLeadStatusPreset();
  const deleteStatusPreset = useDeleteLeadStatusPreset();
  const [manageStatusesOpen, setManageStatusesOpen] = useState(false);
  const [newStatusName, setNewStatusName] = useState("");
  const [statusFieldError, setStatusFieldError] = useState<string | null>(null);
  // Which follow-up card's status dropdown opened the "create new status"
  // dialog — all three share one dialog/preset pool, so this is how a newly
  // created preset gets applied to the right one on success.
  const [pendingStatusTarget, setPendingStatusTarget] = useState<
    "followUp1" | "followUp2" | "followUp3"
  >("followUp1");

  const [status, setStatus] = useState<LeadStatus>("new");
  const [followUpAt, setFollowUpAt] = useState("");
  const [followUpNote, setFollowUpNote] = useState("");
  const [followUp2Status, setFollowUp2Status] = useState<LeadStatus>("new");
  const [followUp2At, setFollowUp2At] = useState("");
  const [followUp2Note, setFollowUp2Note] = useState("");
  const [followUp3Status, setFollowUp3Status] = useState<LeadStatus>("new");
  const [followUp3At, setFollowUp3At] = useState("");
  const [followUp3Note, setFollowUp3Note] = useState("");
  const [tagsInput, setTagsInput] = useState("");

  useEffect(() => {
    if (!lead.data) return;
    setStatus(lead.data.status);
    setFollowUpAt(toDatetimeLocal(lead.data.follow_up_at));
    setFollowUpNote(lead.data.follow_up_note ?? "");
    setFollowUp2Status((lead.data.follow_up_2_status as LeadStatus | null) ?? "new");
    setFollowUp2At(toDatetimeLocal(lead.data.follow_up_2_at));
    setFollowUp2Note(lead.data.follow_up_2_note ?? "");
    setFollowUp3Status((lead.data.follow_up_3_status as LeadStatus | null) ?? "new");
    setFollowUp3At(toDatetimeLocal(lead.data.follow_up_3_at));
    setFollowUp3Note(lead.data.follow_up_3_note ?? "");
    setTagsInput((lead.data.tags ?? []).join(", "));
  }, [lead.data]);

  function handleSave() {
    updateLead.mutate(
      {
        id,
        status,
        follow_up_at: followUpAt ? new Date(followUpAt).toISOString() : null,
        follow_up_note: followUpNote.trim() ? followUpNote.trim() : null,
      },
      {
        onSuccess: () => {
          toast({ title: "Lead updated", variant: "success" });
        },
        onError: (err) => {
          toast({ title: "Update failed", description: err.message, variant: "error" });
        },
      },
    );
  }

  async function handleDelete() {
    if (!lead.data) return;
    const label = lead.data.name ?? formatPhone(lead.data.phone);
    const ok = await confirm({
      title: "Delete lead",
      description: `Delete ${label}? This can't be undone.`,
    });
    if (!ok) return;
    deleteLead.mutate(id, {
      onSuccess: () => {
        toast({ title: "Lead deleted", variant: "success" });
        router.push(backHref);
      },
      onError: (err) =>
        toast({ title: "Could not delete lead", description: err.message, variant: "error" }),
    });
  }

  function openManageStatuses(target: "followUp1" | "followUp2" | "followUp3") {
    setPendingStatusTarget(target);
    setManageStatusesOpen(true);
  }

  function handleAddStatusPreset() {
    const name = newStatusName.trim();
    if (!name) {
      setStatusFieldError("Enter a status name");
      return;
    }
    if (name.length > 20) {
      setStatusFieldError("Keep it under 20 characters");
      return;
    }
    setStatusFieldError(null);
    createStatusPreset.mutate(
      { name },
      {
        onSuccess: (preset) => {
          setNewStatusName("");
          if (pendingStatusTarget === "followUp1") setStatus(preset.name as LeadStatus);
          else if (pendingStatusTarget === "followUp2") setFollowUp2Status(preset.name as LeadStatus);
          else setFollowUp3Status(preset.name as LeadStatus);
          toast({ title: "Status added", variant: "success" });
        },
        onError: (err) => setStatusFieldError(err.message),
      }
    );
  }

  function handleDeleteStatusPreset(presetId: string) {
    deleteStatusPreset.mutate(presetId, {
      onError: (err) =>
        toast({ title: "Couldn't remove status", description: err.message, variant: "error" }),
    });
  }

  function handleSaveTags() {
    const tags = tagsInput
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    updateLead.mutate(
      { id, tags: tags.length > 0 ? tags : null },
      {
        onSuccess: () => {
          toast({ title: "Tags updated", variant: "success" });
        },
        onError: (err) => {
          toast({ title: "Update failed", description: err.message, variant: "error" });
        },
      },
    );
  }

  function handleSaveFollowUp2() {
    updateLead.mutate(
      {
        id,
        follow_up_2_status: followUp2Status,
        follow_up_2_at: followUp2At ? new Date(followUp2At).toISOString() : null,
        follow_up_2_note: followUp2Note.trim() ? followUp2Note.trim() : null,
      },
      {
        onSuccess: () => toast({ title: "Follow up 2 updated", variant: "success" }),
        onError: (err) =>
          toast({ title: "Update failed", description: err.message, variant: "error" }),
      },
    );
  }

  function handleSaveFollowUp3() {
    updateLead.mutate(
      {
        id,
        follow_up_3_status: followUp3Status,
        follow_up_3_at: followUp3At ? new Date(followUp3At).toISOString() : null,
        follow_up_3_note: followUp3Note.trim() ? followUp3Note.trim() : null,
      },
      {
        onSuccess: () => toast({ title: "Follow up 3 updated", variant: "success" }),
        onError: (err) =>
          toast({ title: "Update failed", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <Link
        href={backHref}
        className="mb-4 inline-flex items-center gap-1.5 rounded-md text-sm text-slate-500 transition-colors hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        <ArrowLeft size={15} aria-hidden />
        {backLabel}
      </Link>

      <QueryBoundary
        isLoading={lead.isLoading}
        isError={lead.isError}
        error={lead.error}
        onRetry={() => lead.refetch()}
        loadingFallback={<Skeleton className="h-64 w-full rounded-xl" />}
      >
        {lead.data && (
          <>
            <PageHeader
              title={lead.data.name ?? formatPhone(lead.data.phone)}
              description={formatPhone(lead.data.phone)}
              action={
                <div className="flex items-center gap-2">
                  {lead.data.channel && <ChannelBadge channel={lead.data.channel} />}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleDelete}
                    loading={deleteLead.isPending}
                  >
                    {!deleteLead.isPending && <Trash2 size={14} aria-hidden />}
                    Delete lead
                  </Button>
                </div>
              }
            />

            <div className="flex flex-col gap-5">
              <Card>
                <CardHeader>
                  <CardTitle>Lead Details</CardTitle>
                </CardHeader>
                <CardContent>
                  <dl className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <dt className="text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
                        Intent
                      </dt>
                      <dd className="mt-1">
                        <IntentBadge intent={lead.data.intent} />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
                        Captured
                      </dt>
                      <dd className="mt-1 text-slate-700 dark:text-slate-300">
                        {formatDateTime(lead.data.created_at)}
                      </dd>
                    </div>
                  </dl>
                </CardContent>
              </Card>

              {lead.data.claimed_by_account_user_id && (
                <Card>
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <UserCircle size={15} aria-hidden className="text-slate-400" />
                      <CardTitle>Assigned To</CardTitle>
                    </div>
                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      Auto-assigned when the AI transferred this to a team member — not editable.
                    </p>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-slate-700 dark:text-slate-300">
                      {lead.data.claimed_by_name ?? "—"}
                    </p>
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <CalendarClock size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Follow up 1</CardTitle>
                  </div>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    Where this lead sits in your sales pipeline.
                  </p>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-4">
                    <div>
                      <div className="flex items-center justify-between gap-2">
                        <Label htmlFor="lead-status">Status</Label>
                        <button
                          type="button"
                          onClick={() => openManageStatuses("followUp1")}
                          className="text-xs font-medium text-primary-600 hover:underline dark:text-primary-400"
                        >
                          + Create new status
                        </button>
                      </div>
                      <Dialog open={manageStatusesOpen} onOpenChange={setManageStatusesOpen}>
                          <DialogContent>
                            <DialogTitle>Custom pipeline statuses</DialogTitle>
                            <DialogBody>
                              <p className="text-xs text-slate-500 dark:text-slate-400">
                                Added statuses show up in this dropdown for everyone in your
                                organization.
                              </p>
                              <div className="mt-3 space-y-2">
                                {statusPresets.length === 0 && (
                                  <p className="text-xs text-slate-400">
                                    No custom statuses yet.
                                  </p>
                                )}
                                {statusPresets.map((p) => (
                                  <div
                                    key={p.id}
                                    className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 p-2.5 dark:border-slate-800"
                                  >
                                    <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
                                      {p.name}
                                    </span>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="sm"
                                      onClick={() => handleDeleteStatusPreset(p.id)}
                                    >
                                      Delete
                                    </Button>
                                  </div>
                                ))}
                              </div>
                              <div className="mt-4 space-y-2 border-t border-slate-100 pt-4 dark:border-slate-800">
                                <Label htmlFor="new-status-name" required>
                                  New status name
                                </Label>
                                <Input
                                  id="new-status-name"
                                  value={newStatusName}
                                  onChange={(e) => setNewStatusName(e.target.value)}
                                  placeholder="e.g. Negotiating"
                                  maxLength={20}
                                  aria-invalid={statusFieldError ? true : undefined}
                                />
                                {statusFieldError && (
                                  <p className="text-xs text-red-600">{statusFieldError}</p>
                                )}
                                <Button
                                  type="button"
                                  size="sm"
                                  disabled={createStatusPreset.isPending}
                                  onClick={handleAddStatusPreset}
                                >
                                  {createStatusPreset.isPending ? "Adding..." : "Add status"}
                                </Button>
                              </div>
                            </DialogBody>
                            <DialogFooter>
                              <DialogClose>
                                <Button type="button" variant="ghost">
                                  Done
                                </Button>
                              </DialogClose>
                            </DialogFooter>
                          </DialogContent>
                        </Dialog>
                      <Select
                        id="lead-status"
                        value={status}
                        onChange={(v) => setStatus(v as LeadStatus)}
                        className="w-full"
                      >
                        {LEAD_STATUS_OPTIONS.map((s) => (
                          <option key={s} value={s}>
                            {LEAD_STATUS_LABELS[s]}
                          </option>
                        ))}
                        {statusPresets.map((p) => (
                          <option key={p.id} value={p.name}>
                            {p.name}
                          </option>
                        ))}
                      </Select>
                    </div>

                    <div>
                      <Label htmlFor="follow-up-at">Follow-up date</Label>
                      <input
                        id="follow-up-at"
                        type="datetime-local"
                        value={followUpAt}
                        onChange={(e) => setFollowUpAt(e.target.value)}
                        className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                      />
                    </div>

                    <div>
                      <Label htmlFor="follow-up-note">Follow-up note</Label>
                      <Textarea
                        id="follow-up-note"
                        rows={3}
                        placeholder="e.g. Call back after 5pm, wants a demo…"
                        value={followUpNote}
                        onChange={(e) => setFollowUpNote(e.target.value)}
                      />
                    </div>

                    <Button
                      variant="primary"
                      className="self-start"
                      loading={updateLead.isPending}
                      onClick={handleSave}
                    >
                      {!updateLead.isPending && <Save size={15} aria-hidden />}
                      Save
                    </Button>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <CalendarClock size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Follow up 2</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-4">
                    <div>
                      <div className="flex items-center justify-between gap-2">
                        <Label htmlFor="follow-up-2-status">Status</Label>
                        <button
                          type="button"
                          onClick={() => openManageStatuses("followUp2")}
                          className="text-xs font-medium text-primary-600 hover:underline dark:text-primary-400"
                        >
                          + Create new status
                        </button>
                      </div>
                      <Select
                        id="follow-up-2-status"
                        value={followUp2Status}
                        onChange={(v) => setFollowUp2Status(v as LeadStatus)}
                        className="w-full"
                      >
                        {LEAD_STATUS_OPTIONS.map((s) => (
                          <option key={s} value={s}>
                            {LEAD_STATUS_LABELS[s]}
                          </option>
                        ))}
                        {statusPresets.map((p) => (
                          <option key={p.id} value={p.name}>
                            {p.name}
                          </option>
                        ))}
                      </Select>
                    </div>

                    <div>
                      <Label htmlFor="follow-up-2-at">Follow-up date</Label>
                      <input
                        id="follow-up-2-at"
                        type="datetime-local"
                        value={followUp2At}
                        onChange={(e) => setFollowUp2At(e.target.value)}
                        className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                      />
                    </div>

                    <div>
                      <Label htmlFor="follow-up-2-note">Follow-up note</Label>
                      <Textarea
                        id="follow-up-2-note"
                        rows={3}
                        placeholder="e.g. Call back after 5pm, wants a demo…"
                        value={followUp2Note}
                        onChange={(e) => setFollowUp2Note(e.target.value)}
                      />
                    </div>

                    <Button
                      variant="primary"
                      className="self-start"
                      loading={updateLead.isPending}
                      onClick={handleSaveFollowUp2}
                    >
                      {!updateLead.isPending && <Save size={15} aria-hidden />}
                      Save
                    </Button>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <CalendarClock size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Follow up 3</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-4">
                    <div>
                      <div className="flex items-center justify-between gap-2">
                        <Label htmlFor="follow-up-3-status">Status</Label>
                        <button
                          type="button"
                          onClick={() => openManageStatuses("followUp3")}
                          className="text-xs font-medium text-primary-600 hover:underline dark:text-primary-400"
                        >
                          + Create new status
                        </button>
                      </div>
                      <Select
                        id="follow-up-3-status"
                        value={followUp3Status}
                        onChange={(v) => setFollowUp3Status(v as LeadStatus)}
                        className="w-full"
                      >
                        {LEAD_STATUS_OPTIONS.map((s) => (
                          <option key={s} value={s}>
                            {LEAD_STATUS_LABELS[s]}
                          </option>
                        ))}
                        {statusPresets.map((p) => (
                          <option key={p.id} value={p.name}>
                            {p.name}
                          </option>
                        ))}
                      </Select>
                    </div>

                    <div>
                      <Label htmlFor="follow-up-3-at">Follow-up date</Label>
                      <input
                        id="follow-up-3-at"
                        type="datetime-local"
                        value={followUp3At}
                        onChange={(e) => setFollowUp3At(e.target.value)}
                        className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                      />
                    </div>

                    <div>
                      <Label htmlFor="follow-up-3-note">Follow-up note</Label>
                      <Textarea
                        id="follow-up-3-note"
                        rows={3}
                        placeholder="e.g. Call back after 5pm, wants a demo…"
                        value={followUp3Note}
                        onChange={(e) => setFollowUp3Note(e.target.value)}
                      />
                    </div>

                    <Button
                      variant="primary"
                      className="self-start"
                      loading={updateLead.isPending}
                      onClick={handleSaveFollowUp3}
                    >
                      {!updateLead.isPending && <Save size={15} aria-hidden />}
                      Save
                    </Button>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Tag size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Tags</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-4">
                    {lead.data.tags && lead.data.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {lead.data.tags.map((t) => (
                          <Badge key={t} variant="neutral" icon={null}>
                            {t}
                          </Badge>
                        ))}
                      </div>
                    )}
                    <div>
                      <Label htmlFor="lead-tags">Edit tags</Label>
                      <Input
                        id="lead-tags"
                        value={tagsInput}
                        onChange={(e) => setTagsInput(e.target.value)}
                        placeholder="hot, enterprise, needs-demo"
                      />
                      <p className="mt-1 text-xs text-slate-400 dark:text-slate-600">
                        Comma-separated.
                      </p>
                    </div>
                    <Button
                      variant="primary"
                      className="self-start"
                      loading={updateLead.isPending}
                      onClick={handleSaveTags}
                    >
                      {!updateLead.isPending && <Save size={15} aria-hidden />}
                      Save tags
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </QueryBoundary>
    </div>
  );
}
