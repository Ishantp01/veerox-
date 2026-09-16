"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Building2, Download, Search } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  EmptyState,
  Input,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useToast,
} from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useAdminOrgs } from "@/lib/hooks/useAdminOrgs";
import { downloadCsv } from "@/lib/download-csv";
import { NewOrgDialog } from "@/components/organizations/new-org-dialog";
import { EditOrgDialog } from "@/components/organizations/edit-org-dialog";
import { RegenerateTokenDialog } from "@/components/organizations/regenerate-token-dialog";
import { ManageLicenseDialog } from "@/components/organizations/manage-license-dialog";
import { QuickRenewButton } from "@/components/organizations/quick-renew-button";
import { DeleteOrgDialog } from "@/components/organizations/delete-org-dialog";
import type { AdminOrg } from "@/lib/hooks/useAdminOrgs";
import { EXPIRING_SOON_DAYS, isExpiringSoon } from "@/lib/license-expiry";

const STATUS_BADGE: Record<AdminOrg["license_status"], "success" | "danger" | "neutral"> = {
  active: "success",
  suspended: "danger",
  expired: "danger",
};

/**
 * Platform-wide org directory — every org that's ever signed up, visible
 * only to the platform admin (see apps/api/routers/billing.py's
 * PlatformAdminDep on GET /billing/orgs). A regular customer never reaches
 * this page or sees any org besides their own — every non-admin route is
 * scoped to the caller's own org.
 */
export default function OrganizationsPage() {
  const { user, status } = useAuth();
  const router = useRouter();
  const { data, isLoading, isError, error, refetch } = useAdminOrgs();
  const allOrgs = useMemo(() => data ?? [], [data]);
  const { toast } = useToast();
  const [exporting, setExporting] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (status === "authenticated" && !user?.is_superuser) {
      router.replace("/");
    }
  }, [status, user, router]);

  const expiringSoonCount = useMemo(
    () => allOrgs.filter((org) => isExpiringSoon(org, EXPIRING_SOON_DAYS)).length,
    [allOrgs]
  );

  const orgs = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allOrgs;
    return allOrgs.filter(
      (org) =>
        org.name.toLowerCase().includes(q) ||
        (org.admin_name ?? "").toLowerCase().includes(q) ||
        (org.admin_email ?? "").toLowerCase().includes(q)
    );
  }, [allOrgs, query]);

  if (!user?.is_superuser) return null;

  async function handleExport() {
    setExporting(true);
    try {
      const stamp = new Date().toISOString().slice(0, 10);
      await downloadCsv("/billing/orgs.xlsx", `organizations-${stamp}.xlsx`);
      toast({ title: "Export started", description: "Your Excel download is ready.", variant: "success" });
    } catch (err: unknown) {
      toast({
        title: "Export failed",
        description: err instanceof Error ? err.message : "Could not export organizations.",
        variant: "error",
      });
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Organizations"
        description="Every organization on the platform — visible only to platform admins."
        action={
          <div className="flex items-center gap-2">
            <Link href="/organizations/expiring">
              <Button variant="outline" size="sm">
                <AlertTriangle size={14} aria-hidden />
                Expiring soon
                {expiringSoonCount > 0 && (
                  <Badge variant="danger" className="ml-1">
                    {expiringSoonCount}
                  </Badge>
                )}
              </Button>
            </Link>
            <Button variant="outline" size="sm" onClick={handleExport} loading={exporting}>
              {!exporting && <Download size={14} aria-hidden />}
              Export
            </Button>
            <NewOrgDialog />
          </div>
        }
      />

      <div className="mb-4 max-w-sm">
        <div className="relative">
          <Search
            size={15}
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by organization, admin name, or email"
            className="pl-9"
            aria-label="Search organizations"
          />
        </div>
      </div>

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
                <SkeletonRows rows={5} cols={8} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          query.trim() ? (
            <EmptyState
              icon={Search}
              title="No matching organizations"
              description={`Nothing matches "${query.trim()}" — try a different name or email.`}
            />
          ) : (
            <EmptyState icon={Building2} title="No organizations yet" description="Orgs appear here as they sign up." />
          )
        }
      >
        <div
          data-tour="page-table"
          className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900"
        >
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Organization</TableHeader>
                <TableHeader>Admin</TableHeader>
                <TableHeader>Email</TableHeader>
                <TableHeader>License</TableHeader>
                <TableHeader>Expires</TableHeader>
                <TableHeader>Team members</TableHeader>
                <TableHeader>Created</TableHeader>
                <TableHeader className="text-right">Actions</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {orgs.map((org) => (
                <TableRow key={org.id}>
                  <TableCell className="font-medium text-slate-900 dark:text-slate-100">
                    {org.name}
                  </TableCell>
                  <TableCell>{org.admin_name ?? "—"}</TableCell>
                  <TableCell>{org.admin_email ?? "—"}</TableCell>
                  <TableCell>
                    <Badge variant={STATUS_BADGE[org.license_status]}>{org.license_status}</Badge>
                  </TableCell>
                  <TableCell>
                    {org.license_expires_at ? new Date(org.license_expires_at).toLocaleDateString() : "—"}
                  </TableCell>
                  <TableCell>{org.seat_count}</TableCell>
                  <TableCell>{new Date(org.created_at).toLocaleDateString()}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <QuickRenewButton org={org} />
                      <ManageLicenseDialog org={org} />
                      <EditOrgDialog org={org} />
                      <RegenerateTokenDialog orgId={org.id} orgName={org.name} />
                      <DeleteOrgDialog orgId={org.id} orgName={org.name} />
                    </div>
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
