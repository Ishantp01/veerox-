"use client";

import { useState } from "react";
import Link from "next/link";
import { CalendarCheck, Check, Pencil, Plus, Search } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogTitle,
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
  useToast,
} from "@/components/ui";
import { formatDateTime, formatPhone } from "@/lib/format";
import {
  followUpKey,
  useLeadFollowUps,
  useLeads,
  useUpdateLeadFollowUp,
  type LeadFollowUp,
} from "@/lib/hooks";

type Bucket = "all" | "due" | "today" | "upcoming";

const TABS: { id: Bucket; label: string }[] = [
  { id: "all", label: "All" },
  { id: "due", label: "Due" },
  { id: "today", label: "Today" },
  { id: "upcoming", label: "Upcoming" },
];

function endOfToday(): number {
  const d = new Date();
  d.setHours(23, 59, 59, 999);
  return d.getTime();
}

function bucketOf(f: LeadFollowUp, now: number): Exclude<Bucket, "all"> {
  const at = new Date(f.follow_up_at).getTime();
  if (at <= now) return "due";
  return at <= endOfToday() ? "today" : "upcoming";
}

function toDatetimeLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function BucketBadge({ bucket }: { bucket: Exclude<Bucket, "all"> }) {
  if (bucket === "due")
    return (
      <Badge variant="danger" icon={null}>
        Due
      </Badge>
    );
  if (bucket === "today")
    return (
      <Badge variant="live" icon={null}>
        Today
      </Badge>
    );
  return (
    <Badge variant="neutral" icon={null}>
      Upcoming
    </Badge>
  );
}

function EditDialog({
  followUp,
  onClose,
}: {
  followUp: LeadFollowUp;
  onClose: () => void;
}) {
  const update = useUpdateLeadFollowUp();
  const { toast } = useToast();
  const [at, setAt] = useState(toDatetimeLocal(followUp.follow_up_at));
  const [note, setNote] = useState(followUp.note ?? "");

  function save() {
    if (!at) return;
    update.mutate(
      {
        leadId: followUp.lead_id,
        slot: followUp.slot,
        at: new Date(at).toISOString(),
        note: note.trim() ? note.trim() : null,
      },
      {
        onSuccess: () => {
          toast({ title: "Follow-up updated", variant: "success" });
          onClose();
        },
        onError: (err) =>
          toast({
            title: "Update failed",
            description: err.message,
            variant: "error",
          }),
      },
    );
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogTitle>
          Follow-up {followUp.slot} —{" "}
          {followUp.lead_name || formatPhone(followUp.lead_phone)}
        </DialogTitle>
        <DialogBody>
          <div className="space-y-4">
            <div>
              <Label htmlFor="ft-at">Date &amp; time</Label>
              <input
                id="ft-at"
                type="datetime-local"
                value={at}
                onChange={(e) => setAt(e.target.value)}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <Label htmlFor="ft-note">Note</Label>
              <Textarea
                id="ft-note"
                rows={3}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. Call back after 5pm, wants a demo…"
              />
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <DialogClose>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button onClick={save} loading={update.isPending} disabled={!at}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const DATETIME_INPUT_CLASS =
  "w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";

/** Pick a lead and schedule (or overwrite) one of its three follow-up slots. */
function ScheduleDialog({ onClose }: { onClose: () => void }) {
  const update = useUpdateLeadFollowUp();
  const { toast } = useToast();
  const [leadSearch, setLeadSearch] = useState("");
  const [leadId, setLeadId] = useState("");
  const [slot, setSlot] = useState<"1" | "2" | "3">("1");
  const [at, setAt] = useState("");
  const [note, setNote] = useState("");
  const leads = useLeads({ search: leadSearch.trim() || undefined, limit: 25 });

  function save() {
    if (!leadId || !at) return;
    update.mutate(
      {
        leadId,
        slot: Number(slot) as 1 | 2 | 3,
        at: new Date(at).toISOString(),
        note: note.trim() ? note.trim() : null,
      },
      {
        onSuccess: () => {
          toast({ title: "Follow-up scheduled", variant: "success" });
          onClose();
        },
        onError: (err) =>
          toast({
            title: "Couldn't schedule",
            description: err.message,
            variant: "error",
          }),
      },
    );
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogTitle>Schedule a follow-up</DialogTitle>
        <DialogBody>
          <div className="space-y-4">
            <div>
              <Label htmlFor="sf-lead-search">Lead</Label>
              <Input
                id="sf-lead-search"
                type="search"
                value={leadSearch}
                onChange={(e) => setLeadSearch(e.target.value)}
                placeholder="Search by name or phone…"
                className="mb-2"
              />
              <Select
                value={leadId}
                onChange={setLeadId}
                aria-label="Choose lead"
                className="w-full"
              >
                <option value="">Select a lead…</option>
                {(leads.data ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name || "Unnamed"} — {formatPhone(l.phone)}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="sf-slot">Follow-up number</Label>
              <Select
                id="sf-slot"
                value={slot}
                onChange={(v) => setSlot(v as "1" | "2" | "3")}
                className="w-full"
              >
                <option value="1">Follow-up 1</option>
                <option value="2">Follow-up 2</option>
                <option value="3">Follow-up 3</option>
              </Select>
              <p className="mt-1 text-xs text-slate-400 dark:text-slate-600">
                Choosing a slot that is already scheduled replaces its date and
                note.
              </p>
            </div>
            <div>
              <Label htmlFor="sf-at">Date &amp; time</Label>
              <input
                id="sf-at"
                type="datetime-local"
                value={at}
                onChange={(e) => setAt(e.target.value)}
                className={DATETIME_INPUT_CLASS}
              />
            </div>
            <div>
              <Label htmlFor="sf-note">Note</Label>
              <Textarea
                id="sf-note"
                rows={3}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. Call back after 5pm, wants a demo…"
              />
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <DialogClose>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button
            onClick={save}
            loading={update.isPending}
            disabled={!leadId || !at}
          >
            Schedule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Follow-up Tasks — every scheduled lead follow-up (slots 1–3) in one list,
 * so nobody has to open each lead to see what's coming up. Scoping is done
 * server-side: admins see the whole org, team members only the leads they
 * claimed. Schedule, edit or complete follow-ups here.
 */
export function FollowUpTasksView() {
  const [tab, setTab] = useState<Bucket>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<LeadFollowUp | null>(null);
  const [scheduling, setScheduling] = useState(false);
  const { data, isLoading, isError, error, refetch } = useLeadFollowUps();
  const update = useUpdateLeadFollowUp();
  const { toast } = useToast();

  const now = Date.now();
  const all = data ?? [];
  const term = search.trim().toLowerCase();
  const searched = term
    ? all.filter((f) =>
        [f.lead_name, f.lead_phone, f.note, f.claimed_by_name].some((v) =>
          (v ?? "").toLowerCase().includes(term),
        ),
      )
    : all;
  const counts = { all: searched.length, due: 0, today: 0, upcoming: 0 };
  searched.forEach((f) => (counts[bucketOf(f, now)] += 1));
  const rows =
    tab === "all" ? searched : searched.filter((f) => bucketOf(f, now) === tab);

  function markDone(f: LeadFollowUp) {
    update.mutate(
      { leadId: f.lead_id, slot: f.slot, at: null },
      {
        onSuccess: () =>
          toast({ title: "Follow-up marked done", variant: "success" }),
        onError: (err) =>
          toast({
            title: "Couldn't update",
            description: err.message,
            variant: "error",
          }),
      },
    );
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Follow-up Tasks"
        description="Every scheduled lead follow-up in one place. You'll get a reminder popup when one comes due."
        action={
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => setScheduling(true)}>
              <Plus size={15} aria-hidden /> Schedule follow-up
            </Button>
            <div className="relative">
              <Search
                size={14}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <Input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search lead, phone or note…"
                aria-label="Search follow-up tasks"
                className="w-60 pl-8"
              />
            </div>
          </div>
        }
      />

      <div
        role="tablist"
        aria-label="Follow-up timing"
        className="mb-4 flex flex-wrap gap-2"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={
              "rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 " +
              (tab === t.id
                ? "bg-primary-600 text-white"
                : "bg-white text-slate-600 ring-1 ring-inset ring-slate-200 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-300 dark:ring-slate-700 dark:hover:bg-slate-800")
            }
          >
            {t.label} <span className="opacity-70">{counts[t.id]}</span>
          </button>
        ))}
      </div>

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={rows.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={4} cols={7} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState
            icon={CalendarCheck}
            title="No follow-ups here"
            description="Set a follow-up date on a lead and it will show up in this list."
          />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>When</TableHeader>
                <TableHeader>Lead</TableHeader>
                <TableHeader>Follow-up</TableHeader>
                <TableHeader>Note</TableHeader>
                <TableHeader>Status</TableHeader>
                <TableHeader>Claimed</TableHeader>
                <TableHeader>Actions</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {rows.map((f) => (
                <TableRow key={followUpKey(f)}>
                  <TableCell>
                    <div className="flex flex-col items-start gap-1">
                      <BucketBadge bucket={bucketOf(f, now)} />
                      <span className="text-xs text-slate-500">
                        {formatDateTime(f.follow_up_at)}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Link
                      href={`/crm/leads/${f.lead_id}`}
                      className="rounded-sm text-sm font-semibold text-primary-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:text-primary-400"
                    >
                      {f.lead_name || "Unnamed lead"}
                    </Link>
                    <div className="font-mono text-xs text-slate-500">
                      {formatPhone(f.lead_phone)}
                    </div>
                  </TableCell>
                  <TableCell className="text-sm text-slate-700 dark:text-slate-300">
                    #{f.slot}
                  </TableCell>
                  <TableCell className="max-w-xs truncate text-slate-700 dark:text-slate-300">
                    {f.note || (
                      <span className="text-slate-300 dark:text-slate-600">
                        —
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-slate-600 dark:text-slate-400">
                    {f.status ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs font-medium text-slate-600 dark:text-slate-400">
                    {f.claimed_by_name ?? "—"}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setEditing(f)}
                        aria-label="Edit follow-up"
                      >
                        <Pencil size={14} aria-hidden />
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => markDone(f)}
                        disabled={update.isPending}
                      >
                        <Check size={14} aria-hidden /> Done
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>

      {scheduling && <ScheduleDialog onClose={() => setScheduling(false)} />}
      {editing && (
        <EditDialog followUp={editing} onClose={() => setEditing(null)} />
      )}
    </div>
  );
}
