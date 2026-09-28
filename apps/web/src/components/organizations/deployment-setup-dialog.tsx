"use client";

import { useState } from "react";
import { Settings2 } from "lucide-react";
import {
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  useToast,
} from "@/components/ui";
import { SetupInstructionsView } from "./setup-instructions-view";
import { ConnectionStatusBadge } from "./connection-status-badge";
import {
  useDeploymentSyncStatus,
  usePrepareDeploymentSetup,
  useRegenerateSetupToken,
  useRetryDeploymentSync,
  useRevokeDeployment,
  type ConnectionStatus,
  type SetupInstructions,
} from "@/lib/hooks/usePlatformOrgs";

/** Only the fields this dialog actually needs — lets it be reused from any
 * row shape (the merged organization table) without requiring a full
 * PlatformOrg object. */
export interface DeploymentSetupOrg {
  id: string;
  name: string;
  client_deployment_id: string | null;
  connection_status: ConnectionStatus;
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "Never";
}

/**
 * The technical "setup/details" view for one client organization — kept out
 * of the everyday organization list/form on purpose (see
 * docs/multi-tenant-licensing.md's connection-status section). Handles all
 * three deployment-credential actions: preparing setup for an org that has
 * none yet, replacing a lost token, and revoking one.
 */
export function DeploymentSetupDialog({ org }: { org: DeploymentSetupOrg }) {
  const [open, setOpen] = useState(false);
  const [freshSetup, setFreshSetup] = useState<SetupInstructions | null>(null);
  const [confirmingRevoke, setConfirmingRevoke] = useState(false);
  const { toast } = useToast();

  const hasDeployment = !!org.client_deployment_id;
  const syncStatus = useDeploymentSyncStatus(open && hasDeployment ? org.id : null);
  const prepareSetup = usePrepareDeploymentSetup();
  const regenerateToken = useRegenerateSetupToken();
  const retrySync = useRetryDeploymentSync();
  const revoke = useRevokeDeployment();

  function handleClose(next: boolean) {
    setOpen(next);
    if (!next) {
      setFreshSetup(null);
      setConfirmingRevoke(false);
      prepareSetup.reset();
      regenerateToken.reset();
      revoke.reset();
    }
  }

  function handlePrepareSetup() {
    prepareSetup.mutate(org.id, {
      onSuccess: (setup) => {
        setFreshSetup(setup);
        toast({ title: "Setup prepared", variant: "success" });
      },
      onError: (err) => toast({ title: "Could not prepare setup", description: err.message, variant: "error" }),
    });
  }

  function handleRegenerateToken() {
    regenerateToken.mutate(org.id, {
      onSuccess: (setup) => {
        setFreshSetup(setup);
        toast({ title: "New setup token issued — the old one stopped working", variant: "success" });
      },
      onError: (err) => toast({ title: "Could not generate a new token", description: err.message, variant: "error" }),
    });
  }

  function handleRevoke() {
    revoke.mutate(org.id, {
      onSuccess: () => {
        setConfirmingRevoke(false);
        toast({ title: "Deployment revoked", description: `${org.name} can no longer connect.`, variant: "success" });
      },
      onError: (err) => toast({ title: "Could not revoke", description: err.message, variant: "error" }),
    });
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="ghost" size="sm" aria-label={`Hosting setup for ${org.name}`} title="Hosting setup">
          <Settings2 size={14} aria-hidden />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Hosting setup — {org.name}</DialogTitle>
        <DialogBody className="flex flex-col gap-4">
          {freshSetup ? (
            <SetupInstructionsView setup={freshSetup} />
          ) : !hasDeployment ? (
            <>
              <p className="text-sm text-slate-600 dark:text-slate-300">
                This organization has no hosting setup yet — it was created before this existed, or
                setup was never prepared. Preparing it won&apos;t change anything about the
                organization itself.
              </p>
              <Button variant="primary" onClick={handlePrepareSetup} loading={prepareSetup.isPending}>
                Prepare setup
              </Button>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">Status</span>
                <ConnectionStatusBadge status={org.connection_status} />
              </div>
              {syncStatus.data && (
                <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                  <dt className="text-slate-500 dark:text-slate-400">Last checked in</dt>
                  <dd>{formatDate(syncStatus.data.last_seen_at)}</dd>
                  <dt className="text-slate-500 dark:text-slate-400">Last confirmed config</dt>
                  <dd>{formatDate(syncStatus.data.last_sync_at)}</dd>
                  <dt className="text-slate-500 dark:text-slate-400">Waiting to be picked up</dt>
                  <dd>{syncStatus.data.pending_events > 0 ? `${syncStatus.data.pending_events} change(s)` : "None"}</dd>
                  {syncStatus.data.last_sync_error && (
                    <>
                      <dt className="text-slate-500 dark:text-slate-400">Last error</dt>
                      <dd className="text-red-600 dark:text-red-400">{syncStatus.data.last_sync_error}</dd>
                    </>
                  )}
                </dl>
              )}

              <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3 dark:border-slate-700">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => retrySync.mutate(org.id)}
                  loading={retrySync.isPending}
                >
                  Retry sync
                </Button>
                <Button variant="outline" size="sm" onClick={handleRegenerateToken} loading={regenerateToken.isPending}>
                  Generate replacement setup token
                </Button>
                {!confirmingRevoke ? (
                  <Button variant="outline" size="sm" onClick={() => setConfirmingRevoke(true)}>
                    Revoke access
                  </Button>
                ) : (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-red-600 dark:text-red-400">Revoke this client&apos;s access?</span>
                    <Button variant="danger" size="sm" onClick={handleRevoke} loading={revoke.isPending}>
                      Confirm revoke
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setConfirmingRevoke(false)}>
                      Cancel
                    </Button>
                  </div>
                )}
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Replacing the setup token invalidates the previous one immediately — the client&apos;s
                server must be updated with the new one and restarted. This is never needed for
                ordinary licence renewals or feature changes; those sync automatically.
              </p>
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleClose(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
