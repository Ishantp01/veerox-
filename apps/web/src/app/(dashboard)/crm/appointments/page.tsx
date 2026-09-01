"use client";

import { useState } from "react";
import { CalendarClock } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  APPOINTMENT_STATUS_LABELS,
  APPOINTMENT_STATUS_OPTIONS,
} from "@/components/crm/appointment-status-badge";
import { NewAppointmentDialog } from "@/components/crm/new-appointment-dialog";
import { EmptyState, Select, SkeletonRows, Table, TableCell, TableHeader, TableRow } from "@/components/ui";
import { useAppointments, useUpdateAppointment, type AppointmentSort } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import type { AppointmentStatus } from "@/lib/types";

const SELECT_CLS =
  "rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

export default function AppointmentsPage() {
  const [filter, setFilter] = useState<AppointmentStatus | "">("");
  // Newest-first by default, matching how the Leads list sorts.
  const [sort, setSort] = useState<AppointmentSort>("newest");
  const { data, isLoading, isError, error, refetch } = useAppointments({
    ...(filter ? { status: filter } : {}),
    sort,
  });
  const updateAppointment = useUpdateAppointment();
  const appointments = data ?? [];

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Appointments"
        description="Bookings scheduled from calls, WhatsApp, or manually."
        action={
          <div className="flex flex-wrap items-center gap-3">
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
                <SkeletonRows rows={5} cols={6} />
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
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Name</TableHeader>
                <TableHeader>Number</TableHeader>
                <TableHeader>When</TableHeader>
                <TableHeader>Duration</TableHeader>
                <TableHeader>Notes</TableHeader>
                <TableHeader>Status</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {appointments.map((appt) => (
                <TableRow key={appt.id}>
                  <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                    {appt.name ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-slate-500">{appt.phone ?? "—"}</TableCell>
                  <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                    {formatDateTime(appt.scheduled_at)}
                  </TableCell>
                  <TableCell className="text-xs text-slate-500">{appt.duration_minutes} min</TableCell>
                  <TableCell className="max-w-xs truncate text-xs text-slate-600 dark:text-slate-400">
                    {appt.notes ?? "—"}
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
                </TableRow>
              ))}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>
    </div>
  );
}
