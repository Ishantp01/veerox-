"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Building2 } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { Badge, EmptyState, SkeletonRows, Table, TableCell, TableHeader, TableRow } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useAdminOrgs } from "@/lib/hooks/useAdminOrgs";
import { NewOrgDialog } from "@/components/organizations/new-org-dialog";
import { RegenerateTokenDialog } from "@/components/organizations/regenerate-token-dialog";

const STATUS_BADGE: Record<string, "success" | "danger" | "neutral"> = {
  active: "success",
  trialing: "neutral",
  past_due: "danger",
  canceled: "danger",
  incomplete: "danger",
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
  const orgs = data ?? [];

  useEffect(() => {
    if (status === "authenticated" && !user?.is_superuser) {
      router.replace("/");
    }
  }, [status, user, router]);

  if (!user?.is_superuser) return null;

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Organizations"
        description="Every organization on the platform — visible only to platform admins."
        action={<NewOrgDialog />}
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
                <SkeletonRows rows={5} cols={6} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState icon={Building2} title="No organizations yet" description="Orgs appear here as they sign up." />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Organization</TableHeader>
                <TableHeader>Admin</TableHeader>
                <TableHeader>Plan</TableHeader>
                <TableHeader>Status</TableHeader>
                <TableHeader>Seats</TableHeader>
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
                  <TableCell>{org.admin_email ?? "—"}</TableCell>
                  <TableCell>{org.plan_code ?? "No plan"}</TableCell>
                  <TableCell>
                    <Badge variant={STATUS_BADGE[org.billing_status] ?? "neutral"}>
                      {org.billing_status.replace("_", " ")}
                    </Badge>
                  </TableCell>
                  <TableCell>{org.seat_count}</TableCell>
                  <TableCell>{new Date(org.created_at).toLocaleDateString()}</TableCell>
                  <TableCell className="text-right">
                    <RegenerateTokenDialog orgId={org.id} orgName={org.name} />
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
