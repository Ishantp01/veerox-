"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, CalendarClock } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  EmptyState,
  Select,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useAdminOrgs } from "@/lib/hooks/useAdminOrgs";
import { ManageLicenseDialog } from "@/components/organizations/manage-license-dialog";
import { QuickRenewButton } from "@/components/organizations/quick-renew-button";
import { isExpiringSoon } from "@/lib/license-expiry";

const WINDOW_OPTIONS = [
  { value: "7", label: "Next 7 days" },
  { value: "14", label: "Next 14 days" },
  { value: "30", label: "Next 30 days" },
];

function daysUntil(expiresAt: string): number {
  return Math.ceil((new Date(expiresAt).getTime() - Date.now()) / (24 * 60 * 60 * 1000));
}

/**
 * Orgs whose active license expires within a chosen lookahead window —
 * the renewal follow-up queue for the platform admin. Already-expired or
 * suspended orgs aren't here; they're already flagged directly by the
 * License badge on the main Organizations page, this page is specifically
 * for catching one *before* it lapses.
 */
export default function ExpiringLicensesPage() {
  const { user, status } = useAuth();
  const router = useRouter();
  const { data, isLoading, isError, error, refetch } = useAdminOrgs();
  const allOrgs = useMemo(() => data ?? [], [data]);
  const [windowDays, setWindowDays] = useState("14");

  useEffect(() => {
    if (status === "authenticated" && !user?.is_superuser) {
      router.replace("/");
    }
  }, [status, user, router]);

  const orgs = useMemo(() => {
    const days = Number(windowDays);
    return allOrgs
      .filter((org) => isExpiringSoon(org, days))
      .sort(
        (a, b) => new Date(a.license_expires_at!).getTime() - new Date(b.license_expires_at!).getTime()
      );
  }, [allOrgs, windowDays]);

  if (!user?.is_superuser) return null;

  return (
    <div className="mx-auto max-w-5xl">
      <Link
        href="/organizations"
        className="mb-3 inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
      >
        <ArrowLeft size={14} aria-hidden />
        Organizations
      </Link>
      <PageHeader
        title="Licenses expiring soon"
        description="Active licenses due to lapse — renew them before access cuts off."
        action={
          <Select value={windowDays} onChange={setWindowDays} aria-label="Lookahead window">
            {WINDOW_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={orgs.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={4} cols={5} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState
            icon={CalendarClock}
            title="Nothing expiring soon"
            description="No active org licenses fall within this window."
          />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Organization</TableHeader>
                <TableHeader>Admin</TableHeader>
                <TableHeader>Email</TableHeader>
                <TableHeader>Expires</TableHeader>
                <TableHeader className="text-right">Actions</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {orgs.map((org) => {
                const daysLeft = daysUntil(org.license_expires_at!);
                return (
                  <TableRow key={org.id}>
                    <TableCell className="font-medium text-slate-900 dark:text-slate-100">
                      {org.name}
                    </TableCell>
                    <TableCell>{org.admin_name ?? "—"}</TableCell>
                    <TableCell>{org.admin_email ?? "—"}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span>{new Date(org.license_expires_at!).toLocaleDateString()}</span>
                        <Badge variant={daysLeft <= 3 ? "danger" : "neutral"}>
                          {daysLeft === 0 ? "today" : `${daysLeft}d left`}
                        </Badge>
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <QuickRenewButton org={org} />
                        <ManageLicenseDialog org={org} />
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>
    </div>
  );
}
