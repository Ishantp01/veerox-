"use client";

import { useState } from "react";
import { Download, Trash2, Users } from "lucide-react";
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
  useConfirm,
  useToast,
} from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { downloadCsv } from "@/lib/download-csv";
import { useBillingStatus } from "@/lib/hooks/useBilling";
import { useRemoveMember, useTeamMembers, useUpdateMemberRole, type TeamMember } from "@/lib/hooks/useTeam";
import { InviteMemberDialog } from "@/components/team/invite-member-dialog";
import { RegenerateMemberTokenDialog } from "@/components/team/regenerate-member-token-dialog";

async function exportTeamXlsx(): Promise<void> {
  const stamp = new Date().toISOString().slice(0, 10);
  await downloadCsv("/team/members.xlsx", `team-members-${stamp}.xlsx`);
}

const ROLE_BADGE: Record<TeamMember["role"], "voice" | "neutral"> = {
  admin: "voice",
  member: "neutral",
};

/**
 * Self-service team management for the caller's own org — invite, re-role,
 * and remove teammates on their existing login (see apps/api/routers/team.py).
 * Distinct from the platform-admin-only /organizations directory, which
 * lists every org on the platform rather than managing one org's members.
 */
export default function TeamPage() {
  const { user } = useAuth();
  const { data, isLoading, isError, error, refetch } = useTeamMembers();
  const billing = useBillingStatus();
  const updateRole = useUpdateMemberRole();
  const removeMember = useRemoveMember();
  const { toast } = useToast();
  const confirm = useConfirm();
  // The org owner is the account that bought the plan, not a "team member"
  // — hidden from this list entirely (and, per apps/api/routers/team.py's
  // invite_member, exempt from the plan's max_seats count).
  const members = (data ?? []).filter((m) => !m.is_owner);
  const isAdmin = user?.role === "admin";
  const [exporting, setExporting] = useState(false);

  // The platform operator's own org (any member with is_superuser=True) is
  // exempt from every plan limit, max_seats included — see deps.py's
  // `_org_is_platform_admin_owned`. Mirror that here so a superuser doesn't
  // hit a UI-only "limit reached" block the backend would never enforce.
  const isSuperuser = user?.is_superuser === true;
  const maxMembers = billing.data?.plan?.limits.max_seats;
  // A limit of 0 means the plan never included team members at all (same
  // convention as the billing page's usage bars / choose-plan-cards) — the
  // API still refuses any invite either way (apps/api/routers/team.py), but
  // the header shouldn't advertise a confusing "0 / 0 team members used".
  const hasSeatLimit = !isSuperuser && typeof maxMembers === "number" && maxMembers > 0;
  const memberLimitReached =
    !isSuperuser &&
    typeof maxMembers === "number" &&
    billing.data !== undefined &&
    members.length >= maxMembers;

  async function handleExport() {
    setExporting(true);
    try {
      await exportTeamXlsx();
      toast({ title: "Export started", description: "Your Excel download is ready.", variant: "success" });
    } catch (err: unknown) {
      toast({
        title: "Export failed",
        description: err instanceof Error ? err.message : "Could not export team members.",
        variant: "error",
      });
    } finally {
      setExporting(false);
    }
  }

  function handleRoleChange(member: TeamMember, role: string) {
    updateRole.mutate(
      { accountUserId: member.account_user_id, role },
      {
        onError: (err) =>
          toast({ title: "Could not update role", description: err.message, variant: "error" }),
      }
    );
  }

  async function handleRemove(member: TeamMember) {
    const ok = await confirm({
      title: "Remove team member",
      description: `Remove ${member.email} from the team?`,
    });
    if (!ok) return;
    removeMember.mutate(member.account_user_id, {
      onSuccess: () => toast({ title: "Member removed", variant: "success" }),
      onError: (err) =>
        toast({ title: "Could not remove member", description: err.message, variant: "error" }),
    });
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Team"
        description={
          hasSeatLimit
            ? `${members.length} / ${maxMembers} team members used`
            : "Everyone with access to your organization's dashboard."
        }
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleExport} loading={exporting}>
              <Download size={14} aria-hidden />
              Export
            </Button>
            {isAdmin && <InviteMemberDialog disabled={memberLimitReached} />}
          </div>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={members.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={4} cols={6} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState icon={Users} title="No team members yet" description="Invite your first teammate." />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Member</TableHeader>
                <TableHeader>Email</TableHeader>
                <TableHeader>Phone</TableHeader>
                <TableHeader>Role</TableHeader>
                <TableHeader>Joined</TableHeader>
                {isAdmin && <TableHeader className="text-right">Actions</TableHeader>}
              </TableRow>
            </thead>
            <tbody>
              {members.map((member) => (
                <TableRow key={member.account_user_id}>
                  <TableCell className="font-medium text-slate-900 dark:text-slate-100">
                    {member.full_name ?? "—"}
                    {!member.is_active && (
                      <Badge variant="neutral" className="ml-2">
                        inactive
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>{member.email}</TableCell>
                  <TableCell>{member.mobile ?? "—"}</TableCell>
                  <TableCell>
                    {isAdmin ? (
                      <Select
                        value={member.role}
                        onChange={(role) => handleRoleChange(member, role)}
                        aria-label={`Role for ${member.email}`}
                        disabled={updateRole.isPending}
                      >
                        <option value="admin">Admin</option>
                        <option value="member">Member</option>
                      </Select>
                    ) : (
                      <Badge variant={ROLE_BADGE[member.role]}>{member.role}</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    {member.joined_at ? new Date(member.joined_at).toLocaleDateString() : "—"}
                  </TableCell>
                  {isAdmin && (
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <RegenerateMemberTokenDialog
                          accountUserId={member.account_user_id}
                          email={member.email}
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRemove(member)}
                          aria-label={`Remove ${member.email}`}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>
    </div>
  );
}
