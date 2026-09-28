import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

/**
 * A client deployment's connection state, simplified for a non-technical
 * owner (see apps/api/core/deployment_status.py::compute_connection_status):
 * - "awaiting_client_setup": a token exists but the client hasn't checked in yet.
 * - "connected": has authenticated AND confirmed it applied its configuration.
 *   Never shown just because a token was generated.
 * - "sync_pending": has checked in, but hasn't confirmed the latest configuration yet.
 * - "connection_issue": the token was revoked, or it's gone quiet for a while.
 */
export type ConnectionStatus = "awaiting_client_setup" | "connected" | "sync_pending" | "connection_issue";

export interface PlatformOrg {
  id: string;
  central_org_ref: string;
  name: string;
  enabled_features: string[] | null;
  deployment_status: "pending_deployment" | "provisioned";
  config_version: number;
  license_status: "active" | "suspended" | "expired" | "revoked";
  client_deployment_id: string | null;
  connection_status: ConnectionStatus;
}

/** GET /platform/organizations → PlatformOrg[] (platform-admin only) */
export function usePlatformOrgs() {
  return useQuery<PlatformOrg[]>({
    queryKey: ["platform", "orgs"],
    queryFn: () => apiFetch<PlatformOrg[]>("/platform/organizations"),
    // A client's connection status changes on its own (it checks in every
    // few minutes) — poll gently so the owner sees "Connected" land without
    // needing to refresh the page themselves.
    refetchInterval: 30_000,
  });
}

export interface CreateClientOrgInput {
  name: string;
  enabled_features?: string[] | null;
  license_days?: number;
  max_team_members?: number;
  idempotency_key?: string;
}

export interface SetupInstructions {
  central_org_ref: string;
  license_api_url: string;
  // Shown exactly once, here — never retrievable again after this response.
  deployment_token: string;
  // Ready-to-paste block using the exact .env variable names; DATABASE_URL
  // is always a placeholder the client fills in with their own connection.
  env_snippet: string;
}

export interface CreateClientOrgResult {
  organization: PlatformOrg;
  // Only present on the first creation — a duplicate resubmit (same
  // idempotency_key) returns the existing org with setup: null, since the
  // token was already shown once.
  setup: SetupInstructions | null;
}

/**
 * POST /platform/organizations → CreateClientOrgResult (platform-admin
 * only). One call creates the organization, its allocated features, an
 * optional first licence, AND its default client deployment + token — no
 * separate "register deployment" step. Never creates AWS infrastructure by
 * itself.
 */
export function useCreateClientOrg() {
  const queryClient = useQueryClient();
  return useMutation<CreateClientOrgResult, Error, CreateClientOrgInput>({
    mutationFn: (body) =>
      apiFetch<CreateClientOrgResult>("/platform/organizations", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platform", "orgs"] });
    },
  });
}

/**
 * POST /platform/organizations/{id}/deployments → SetupInstructions
 * (platform-admin only). "Prepare setup" for an organization that predates
 * this feature and has no deployment yet — creates the deployment + token
 * without touching any of the organization's existing business data.
 * Refused (409) if a deployment already exists.
 */
export function usePrepareDeploymentSetup() {
  const queryClient = useQueryClient();
  return useMutation<SetupInstructions, Error, string>({
    mutationFn: (orgId) =>
      apiFetch<SetupInstructions>(`/platform/organizations/${orgId}/deployments`, {
        method: "POST",
        body: JSON.stringify({}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platform", "orgs"] });
    },
  });
}

/**
 * POST /platform/organizations/{id}/deployments/regenerate-token →
 * SetupInstructions (platform-admin only). Owner-only "lost the token"
 * recovery — invalidates the previous token immediately (only its hash was
 * ever stored, so it can't be shown again). Never part of ordinary edits,
 * renewals, or restarts.
 */
export function useRegenerateSetupToken() {
  const queryClient = useQueryClient();
  return useMutation<SetupInstructions, Error, string>({
    mutationFn: (orgId) =>
      apiFetch<SetupInstructions>(`/platform/organizations/${orgId}/deployments/regenerate-token`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platform", "orgs"] });
    },
  });
}

/**
 * POST /platform/organizations/{id}/deployments/revoke → void
 * (platform-admin only). Immediately and permanently invalidates this org's
 * deployment credential — its client loses access the instant it next
 * tries to validate.
 */
export function useRevokeDeployment() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (orgId) =>
      apiFetch<void>(`/platform/organizations/${orgId}/deployments/revoke`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platform", "orgs"] });
    },
  });
}

export interface DeploymentSyncStatus {
  id: string;
  org_id: string;
  name: string;
  status: "pending" | "active" | "revoked";
  config_version: number;
  last_seen_at: string | null;
  last_sync_at: string | null;
  last_sync_status: "pending" | "synced" | "failed";
  last_sync_error: string | null;
  pending_events: number;
}

/** GET /platform/organizations/{id}/sync-status → DeploymentSyncStatus
 * (platform-admin only) — the technical "details" view behind the simple
 * connection_status badge. */
export function useDeploymentSyncStatus(orgId: string | null) {
  return useQuery<DeploymentSyncStatus>({
    queryKey: ["platform", "orgs", orgId, "sync-status"],
    queryFn: () => apiFetch<DeploymentSyncStatus>(`/platform/organizations/${orgId}/sync-status`),
    enabled: !!orgId,
  });
}

/** POST /platform/organizations/{id}/sync-status/retry → DeploymentSyncStatus
 * (platform-admin only) — re-queues the org's current configuration for the
 * client to pick up on its next check-in; never pushes anything itself. */
export function useRetryDeploymentSync() {
  const queryClient = useQueryClient();
  return useMutation<DeploymentSyncStatus, Error, string>({
    mutationFn: (orgId) =>
      apiFetch<DeploymentSyncStatus>(`/platform/organizations/${orgId}/sync-status/retry`, {
        method: "POST",
      }),
    onSuccess: (_data, orgId) => {
      queryClient.invalidateQueries({ queryKey: ["platform", "orgs"] });
      queryClient.invalidateQueries({ queryKey: ["platform", "orgs", orgId, "sync-status"] });
    },
  });
}
