"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, BadgeCheck, CalendarClock, MessageSquare, Save, Tag, Users } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import { IntentBadge } from "@/components/leads/intent-badge";
import { LEAD_STATUS_LABELS, LEAD_STATUS_OPTIONS } from "@/components/leads/status-badge";
import {
  LEAD_QUALIFICATION_LABELS,
  LEAD_QUALIFICATION_OPTIONS,
} from "@/components/leads/qualification-badge";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Input,
  Label,
  Select,
  Skeleton,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Textarea,
  useToast,
} from "@/components/ui";
import { useLead, useUpdateLead } from "@/lib/hooks";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { LeadQualificationStatus, LeadStatus } from "@/lib/types";

/** ISO datetime -> value an <input type="datetime-local"> accepts. */
function toDatetimeLocal(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function conversationHref(channel: string, id: string): string {
  return channel === "voice" ? `/calling/conversations/${id}` : `/whatsapp/conversations/${id}`;
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
  const lead = useLead(id);
  const updateLead = useUpdateLead();

  const [status, setStatus] = useState<LeadStatus>("new");
  const [followUpAt, setFollowUpAt] = useState("");
  const [followUpNote, setFollowUpNote] = useState("");
  const [qualificationStatus, setQualificationStatus] = useState<LeadQualificationStatus>("unqualified");
  const [qualificationScore, setQualificationScore] = useState("");
  const [qualificationNotes, setQualificationNotes] = useState("");
  const [tagsInput, setTagsInput] = useState("");

  useEffect(() => {
    if (!lead.data) return;
    setStatus(lead.data.status);
    setFollowUpAt(toDatetimeLocal(lead.data.follow_up_at));
    setFollowUpNote(lead.data.follow_up_note ?? "");
    setQualificationStatus(lead.data.qualification_status);
    setQualificationScore(lead.data.qualification_score?.toString() ?? "");
    setQualificationNotes(lead.data.qualification_notes ?? "");
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

  function handleSaveQualification() {
    updateLead.mutate(
      {
        id,
        qualification_status: qualificationStatus,
        qualification_score: qualificationScore.trim() ? Number(qualificationScore) : null,
        qualification_notes: qualificationNotes.trim() ? qualificationNotes.trim() : null,
      },
      {
        onSuccess: () => {
          toast({ title: "Review stage updated", variant: "success" });
        },
        onError: (err) => {
          toast({ title: "Update failed", description: err.message, variant: "error" });
        },
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
                lead.data.channel ? <ChannelBadge channel={lead.data.channel} /> : undefined
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

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <CalendarClock size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Status &amp; Follow-up</CardTitle>
                  </div>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    Where this lead sits in your sales pipeline.
                  </p>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-4">
                    <div>
                      <Label htmlFor="lead-status">Status</Label>
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
                    <BadgeCheck size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Review Stage</CardTitle>
                  </div>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    A separate, rep-driven review of whether this lead is worth pursuing —
                    independent of the pipeline Status above.
                  </p>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-4">
                    <div>
                      <Label htmlFor="qualification-status">Review stage</Label>
                      <Select
                        id="qualification-status"
                        value={qualificationStatus}
                        onChange={(v) => setQualificationStatus(v as LeadQualificationStatus)}
                        className="w-full"
                      >
                        {LEAD_QUALIFICATION_OPTIONS.map((s) => (
                          <option key={s} value={s}>
                            {LEAD_QUALIFICATION_LABELS[s]}
                          </option>
                        ))}
                      </Select>
                    </div>

                    <div>
                      <Label htmlFor="qualification-score">Score</Label>
                      <input
                        id="qualification-score"
                        type="number"
                        value={qualificationScore}
                        onChange={(e) => setQualificationScore(e.target.value)}
                        placeholder="e.g. 80"
                        className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                      />
                    </div>

                    <div>
                      <Label htmlFor="qualification-notes">Notes</Label>
                      <Textarea
                        id="qualification-notes"
                        rows={3}
                        placeholder="Why this lead is (or isn't) qualified…"
                        value={qualificationNotes}
                        onChange={(e) => setQualificationNotes(e.target.value)}
                      />
                    </div>

                    <Button
                      variant="primary"
                      className="self-start"
                      loading={updateLead.isPending}
                      onClick={handleSaveQualification}
                    >
                      {!updateLead.isPending && <Save size={15} aria-hidden />}
                      Save review stage
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

              <Card>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <MessageSquare size={15} aria-hidden className="text-slate-400" />
                    <CardTitle>Conversation History</CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  {lead.data.conversations.length === 0 ? (
                    <EmptyState
                      icon={Users}
                      title="No conversations yet"
                      description="This lead's conversations will appear here once they talk to the agent."
                      className="border-0"
                    />
                  ) : (
                    <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
                      <Table>
                        <thead>
                          <TableRow isHeader>
                            <TableHeader>Channel</TableHeader>
                            <TableHeader>Started</TableHeader>
                            <TableHeader>Ended</TableHeader>
                            <TableHeader># Messages</TableHeader>
                          </TableRow>
                        </thead>
                        <tbody>
                          {lead.data.conversations.map((c) => {
                            const href = conversationHref(c.channel, c.id);
                            return (
                              <TableRow
                                key={c.id}
                                role="link"
                                tabIndex={0}
                                aria-label={`Open conversation ${c.id}`}
                                onClick={() => router.push(href)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" || e.key === " ") {
                                    e.preventDefault();
                                    router.push(href);
                                  }
                                }}
                                className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
                              >
                                <TableCell>
                                  <ChannelBadge
                                    channel={c.channel === "voice" ? "voice" : "whatsapp"}
                                  />
                                </TableCell>
                                <TableCell className="text-xs text-slate-500">
                                  {formatDateTime(c.started_at)}
                                </TableCell>
                                <TableCell className="text-xs text-slate-500">
                                  {formatDateTime(c.ended_at)}
                                </TableCell>
                                <TableCell className="font-bold text-slate-800 dark:text-slate-100">
                                  {c.message_count ?? "—"}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </tbody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </QueryBoundary>
    </div>
  );
}
