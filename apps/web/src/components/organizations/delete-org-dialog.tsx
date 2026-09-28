"use client";

import { useState } from "react";
import { AlertTriangle, Trash2 } from "lucide-react";
import {
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  Input,
  Label,
  useToast,
} from "@/components/ui";
import { useDeleteOrgAdmin } from "@/lib/hooks/useAdminOrgs";

/**
 * Platform-admin-only: irreversibly deletes another org and everything
 * under it (see DELETE /billing/orgs/{orgId}). Requires typing the org's
 * exact name to enable the button — this destroys every lead, conversation,
 * campaign and teammate under that org in one shot, so the row action needs
 * more friction than a plain yes/no confirm.
 */
export function DeleteOrgDialog({ orgId, orgName }: { orgId: string; orgName: string }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const deleteOrg = useDeleteOrgAdmin();
  const { toast } = useToast();

  const matches = typed.trim() === orgName;

  function handleClose(next: boolean) {
    setOpen(next);
    if (!next) {
      setTyped("");
      deleteOrg.reset();
    }
  }

  function handleDelete() {
    if (!matches) return;
    deleteOrg.mutate(orgId, {
      onSuccess: () => {
        toast({ title: "Organization deleted", variant: "success" });
        handleClose(false);
      },
      onError: (err) =>
        toast({ title: "Could not delete organization", description: err.message, variant: "error" }),
    });
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Delete ${orgName}`}
          className="text-red-500 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-500/10"
        >
          <Trash2 size={14} />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-red-50 text-red-500 dark:bg-red-500/10 dark:text-red-400">
            <AlertTriangle size={15} aria-hidden />
          </span>
          Delete organization
        </DialogTitle>
        <DialogBody className="flex flex-col items-center gap-3 text-center">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            This permanently deletes <strong>{orgName}</strong> — every lead, conversation,
            appointment, campaign, and team member under it. There is no undo.
          </p>
          <div className="w-full">
            <Label htmlFor="delete-org-confirm" className="block text-center">
              Type <strong>{orgName}</strong> to confirm
            </Label>
            <Input
              id="delete-org-confirm"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={orgName}
              autoComplete="off"
              className="text-center"
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => handleClose(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={handleDelete}
            disabled={!matches}
            loading={deleteOrg.isPending}
          >
            Delete organization
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
