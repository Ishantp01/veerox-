import { Badge } from "@/components/ui";
import type { ConnectionStatus } from "@/lib/hooks/usePlatformOrgs";

/**
 * Plain-language label per connection_status — "Connected" is only ever
 * shown once a client has BOTH authenticated and confirmed it applied its
 * configuration, never merely because a setup token was generated.
 */
const LABEL: Record<ConnectionStatus, string> = {
  awaiting_client_setup: "Awaiting client setup",
  connected: "Connected",
  sync_pending: "Sync pending",
  connection_issue: "Connection issue",
};

const VARIANT: Record<ConnectionStatus, "neutral" | "success" | "live" | "danger"> = {
  awaiting_client_setup: "neutral",
  connected: "success",
  sync_pending: "live",
  connection_issue: "danger",
};

export function ConnectionStatusBadge({ status }: { status: ConnectionStatus }) {
  return <Badge variant={VARIANT[status]}>{LABEL[status]}</Badge>;
}
