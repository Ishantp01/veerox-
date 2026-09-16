"use client";

import { useState } from "react";
import { Download, Trash2, Users } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  EmptyState,
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
import { useAuth } from "@/lib/auth-context";
import { downloadCsv } from "@/lib/download-csv";
import { useClientPagination } from "@/lib/hooks";
import { useRemoveMember, useTeamMembers, useUpdateMember, type TeamMember } from "@/lib/hooks/useTeam";
import { InviteMemberDialog } from "@/components/team/invite-member-dialog";
import { EditMemberDialog } from "@/components/team/edit-member-dialog";
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
  const updateMember = useUpdateMember();
  const removeMember = useRemoveMember();
  const { toast } = useToast();
  const confirm = useConfirm();
  // The org owner is the account the org was provisioned under, not a "team
  // member" — hidden from this list entirely.
  const members = (data ?? []).filter((m) => !m.is_owner);
  const pager = useClientPagination(members, 20);
  const isAdmin = user?.role === "admin";
  const [exporting, setExporting] = useState(false);

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
    updateMember.mutate(
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
        description="Everyone with access to your organization's dashboard."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleExport} loading={exporting}>
              <Download size={14} aria-hidden />
              Export
            </Button>
            {isAdmin && <InviteMemberDialog />}
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
        <div
          data-tour="page-table"
          className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900"
        >
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
              {pager.pageRows.map((member) => (
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
                        disabled={updateMember.isPending}
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
                        <EditMemberDialog member={member} />
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
        <Pagination
          page={pager.page}
          pageSize={pager.pageSize}
          rowCount={pager.rowCount}
          hasNextPage={pager.hasNextPage}
          onPrev={pager.onPrev}
          onNext={pager.onNext}
        />
      </QueryBoundary>
    </div>
  );
}
