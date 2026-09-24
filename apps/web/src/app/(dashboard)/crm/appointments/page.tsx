"use client";

import { useState } from "react";
import { CalendarClock, Pencil, Search, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  APPOINTMENT_STATUS_LABELS,
  APPOINTMENT_STATUS_OPTIONS,
} from "@/components/crm/appointment-status-badge";
import { NewAppointmentDialog } from "@/components/crm/new-appointment-dialog";
import { EditAppointmentDialog } from "@/components/crm/edit-appointment-dialog";
import {
  Badge,
  Button,
  EmptyState,
  Input,
  Pagination,
  Select,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useConfirm,
  useToast,
} from "@/components/ui";
import {
  useAppointments,
  useClientPagination,
  useDeleteAppointment,
  useUpdateAppointment,
  type AppointmentSort,
} from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import { useAuth } from "@/lib/auth-context";
import type { Appointment, AppointmentStatus } from "@/lib/types";

const SELECT_CLS =
  "rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

export default function AppointmentsPage() {
  const [filter, setFilter] = useState<AppointmentStatus | "">("");
  // Newest-first by default, matching how the Leads list sorts.
  const [sort, setSort] = useState<AppointmentSort>("newest");
  const [search, setSearch] = useState("");
  const { data, isLoading, isError, error, refetch } = useAppointments({
    ...(filter ? { status: filter } : {}),
    sort,
  });
  const updateAppointment = useUpdateAppointment();
  const deleteAppointment = useDeleteAppointment();
  const { user } = useAuth();
  const scopedToMember = user?.role === "member" && !user?.is_superuser;
  const allAppointments = data ?? [];
  // Client-side — matches name/phone/tags/notes, same "one box searches
  // everything" convention as the Leads page's intent/tag search.
  const term = search.trim().toLowerCase();
  const appointments = term
    ? allAppointments.filter((a) =>
        [a.name, a.phone, a.notes, ...(a.tags ?? [])]
          .filter(Boolean)
          .some((f) => f!.toLowerCase().includes(term)),
      )
    : allAppointments;
  const pager = useClientPagination(appointments, 20, `${filter}|${sort}|${search}`);
  const [editingAppointment, setEditingAppointment] = useState<Appointment | null>(null);
  const confirm = useConfirm();
  const { toast } = useToast();

  async function handleDelete(appt: Appointment) {
    const ok = await confirm({
      title: "Delete appointment",
      description: `Delete the appointment for "${appt.name ?? "this contact"}" on ${formatDateTime(
        appt.scheduled_at,
      )}? This can't be undone.`,
    });
    if (!ok) return;
    deleteAppointment.mutate(appt.id, {
      onSuccess: () => toast({ title: "Appointment deleted", variant: "success" }),
      onError: (err) =>
        toast({ title: "Could not delete appointment", description: err.message, variant: "error" }),
    });
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Appointments"
        description={
          scopedToMember
            ? "Bookings for leads assigned to you, from calls, WhatsApp, or manually."
            : "Bookings scheduled from calls, WhatsApp, or manually."
        }
        action={
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search
                size={14}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <Input
                id="appointment-search"
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, number, or tag…"
                aria-label="Search appointments by name, number, or tag"
                className="w-56 pl-8"
              />
            </div>
            <Select
              value={sort}
              onChange={(v) => setSort(v as AppointmentSort)}
              aria-label="Sort appointments"
            >
              <option value="newest">Newest</option>
              <option value="oldest">Oldest</option>
            </Select>
            <Select
              value={filter}
              onChange={(v) => setFilter(v as AppointmentStatus | "")}
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {APPOINTMENT_STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {APPOINTMENT_STATUS_LABELS[s]}
                </option>
              ))}
            </Select>
            <NewAppointmentDialog />
          </div>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={appointments.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={5} cols={8} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState
            icon={CalendarClock}
            title="No appointments yet"
            description="Book your first appointment to see it here."
          />
        }
      >
        <div
          data-tour="page-table"
          className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900"
        >
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Name</TableHeader>
                <TableHeader>Number</TableHeader>
                <TableHeader>Call Back</TableHeader>
                <TableHeader>Duration</TableHeader>
                <TableHeader>Notes</TableHeader>
                <TableHeader>Tags</TableHeader>
                <TableHeader>Status</TableHeader>
                <TableHeader>Actions</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {pager.pageRows.map((appt) => (
                <TableRow key={appt.id}>
                  <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                    {appt.name ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-slate-500">{appt.phone ?? "—"}</TableCell>
                  <TableCell className="text-xs text-slate-600 dark:text-slate-400">
                    {appt.callback_at ? (
                      <Badge variant="live" icon={null}>
                        {formatDateTime(appt.callback_at)}
                      </Badge>
                    ) : (
                      <span className="text-slate-400">No callback</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-slate-500">{appt.duration_minutes} min</TableCell>
                  <TableCell className="max-w-xs truncate text-xs text-slate-600 dark:text-slate-400">
                    {appt.notes ?? "—"}
                  </TableCell>
                  <TableCell>
                    {appt.tags && appt.tags.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {appt.tags.map((t) => (
                          <Badge key={t} variant="neutral" icon={null}>
                            {t}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Select
                      value={appt.status}
                      onChange={(v) =>
                        updateAppointment.mutate({
                          id: appt.id,
                          status: v as AppointmentStatus,
                        })
                      }
                      className={SELECT_CLS}
                      aria-label={`Status for appointment on ${appt.scheduled_at}`}
                    >
                      {APPOINTMENT_STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {APPOINTMENT_STATUS_LABELS[s]}
                        </option>
                      ))}
                    </Select>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Edit appointment for ${appt.name ?? "contact"}`}
                        onClick={() => setEditingAppointment(appt)}
                      >
                        <Pencil size={14} aria-hidden />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Delete appointment for ${appt.name ?? "contact"}`}
                        onClick={() => handleDelete(appt)}
                      >
                        <Trash2 size={14} aria-hidden />
                      </Button>
                    </div>
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
      <EditAppointmentDialog appointment={editingAppointment} onClose={() => setEditingAppointment(null)} />
    </div>
  );
}
