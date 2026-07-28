// TypeScript types matching the backend's Pydantic schemas (apps/api/schemas/).
// Keep these in sync with the DB models defined in diagrams.md §5.

export interface Conversation {
  id: string;
  user_id: string;
  channel: "voice" | "whatsapp";
  started_at: string;
  ended_at: string | null;
  message_count?: number;
  // Plivo hosts the audio file itself — this is just the URL + duration it
  // reported when the recording finished processing. Voice calls only.
  recording_url?: string | null;
  recording_duration_secs?: number | null;
  // AI-generated summary, produced on demand via POST
  // /admin/conversations/{id}/summarize — null until a rep requests one.
  summary?: string | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  channel: string;
  tokens_in: number | null;
  tokens_out: number | null;
  audio_secs: number | null;
  created_at: string;
}

export type LeadStatus = "new" | "contacted" | "qualified" | "converted" | "lost";

// Separate qualification pipeline from `status` — a lead can be
// status="contacted" while a rep separately works it through this.
export type LeadQualificationStatus = "unqualified" | "in_review" | "qualified" | "disqualified";

export interface Lead {
  id: string;
  org_id: string;
  user_id: string;
  contact_id: string | null;
  name: string | null;
  phone: string | null;
  intent: string | null;
  channel: "voice" | "whatsapp" | null;
  // Backend column is named "metadata" but the SQLAlchemy attribute is
  // `metadata_` (to avoid shadowing SQLAlchemy's DeclarativeBase.metadata).
  // Pydantic serialises it back out as "metadata_" — keep that name here.
  metadata_: Record<string, unknown> | null;
  status: LeadStatus;
  follow_up_at: string | null;
  follow_up_note: string | null;
  qualification_status: LeadQualificationStatus;
  qualification_score: number | null;
  qualification_notes: string | null;
  qualified_at: string | null;
  created_at: string;
}

// GET /admin/leads/{id} — Lead plus its conversation history, joined
// server-side via the shared user_id (Lead has no direct FK to Conversation).
export interface LeadDetail extends Lead {
  conversations: Conversation[];
}

// CRM contact — cross-channel parent entity Leads can optionally roll up
// under via Lead.contact_id. Distinct from the per-channel messaging User.
export interface Contact {
  id: string;
  org_id: string;
  name: string | null;
  phone: string;
  email: string | null;
  company: string | null;
  tags: string[] | null;
  owner_user_id: string | null;
  created_at: string;
  updated_at: string;
}

// GET /crm/contacts/{id} — Contact plus every Lead rolled up under it.
export interface ContactWithLeads extends Contact {
  leads: Lead[];
}

export interface PipelineStage {
  status: LeadStatus;
  count: number;
  value: number;
}

export interface Pipeline {
  stages: PipelineStage[];
  total_value: number;
}

export interface ChannelBreakdown {
  channel: string;
  count: number;
  value: number;
}

export interface QualificationFunnelStage {
  qualification_status: LeadQualificationStatus;
  count: number;
}

export interface RevenueSummary {
  total_pipeline_value: number;
  won_value: number;
  by_channel: ChannelBreakdown[];
  qualification_funnel: QualificationFunnelStage[];
}

export type FollowUpTaskStatus = "pending" | "sending" | "sent" | "failed" | "skipped" | "cancelled";

export interface FollowUpRule {
  id: string;
  org_id: string;
  name: string;
  trigger_type: "status_change";
  trigger_config: { status?: string; delay_hours?: number };
  channel: string;
  message_template: string;
  active: boolean;
  created_at: string;
}

export interface Template {
  id: string;
  org_id: string;
  name: string;
  language: string;
  category: string | null;
  param_labels: string[];
  body_preview: string | null;
  active: boolean;
  created_at: string;
}

export interface FollowUpTask {
  id: string;
  org_id: string;
  lead_id: string;
  rule_id: string | null;
  run_at: string;
  status: FollowUpTaskStatus;
  created_at: string;
  sent_at: string | null;
}

export type AppointmentStatus = "scheduled" | "confirmed" | "completed" | "cancelled" | "no_show";

export interface Appointment {
  id: string;
  org_id: string;
  contact_id: string | null;
  lead_id: string | null;
  scheduled_at: string;
  duration_minutes: number;
  status: AppointmentStatus;
  assigned_user_id: string | null;
  notes: string | null;
  created_at: string;
}

// One entry sitting in the Redis human_handoff_queue. Shape produced by
// apps/api/core/tools.py:transfer_to_human.
export interface HandoffQueueEntry {
  reason: string;
  urgency: string;
  user_id: string | null;
  org_id: string;
  channel?: "voice" | "whatsapp" | null;
  requested_at: string;
}

export interface Stats {
  users_today: number;
  calls_today: number;
  leads_today: number;
  p50_turn_latency_ms: number | null;
  usd_spend_today?: number | null;
  error_count_today?: number | null;
  whatsapp_messages_today?: number | null;
  leads_today_voice?: number | null;
  leads_today_whatsapp?: number | null;
}

export interface Prompts {
  base: string;
  voice_append: string;
  whatsapp_append: string;
}

// Tool JSON schemas exposed by GET /admin/tools.
// Shape is a passthrough of the OpenAI tool definitions stored server-side.
export interface Tool {
  type?: string;
  function?: {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
  };
  // The endpoint returns the raw tool schemas — keep an index signature so
  // the UI can render any extra fields without losing type-safety on the
  // documented ones above.
  [key: string]: unknown;
}

// Response shape from GET /admin/escalations — backend returns both:
//   recent_leads: persisted Lead rows with intent='escalation'
//   queue:       live entries from the Redis human_handoff_queue
// The UI flattens these into a unified display list.
export interface EscalationsResponse {
  recent_leads: Lead[];
  queue: HandoffQueueEntry[];
}

// Unified row shape the escalations table actually renders.
export interface Escalation {
  source: "lead" | "queue";
  id?: string;
  created_at: string;
  user_id: string | null;
  user_phone: string | null;
  reason: string;
  urgency: string;
  conversation_id?: string | null;
}

export interface KillSwitchState {
  enabled: boolean;
}

export interface OutboundWhatsAppResponse {
  status: string;
  phone: string;
  text: string;
  // Meta Graph API message id — null when the backend's META_ACCESS_TOKEN
  // is unset (local-dev fallback returns status="queued" with no real send).
  wa_message_id: string | null;
}

export interface OutboundCallResponse {
  call_sid: string;
  status?: string;
}

// GET /admin/settings/whatsapp — read-only Meta/WhatsApp channel config
// status. Secrets are reported as booleans only; the values themselves live
// in Render env vars, not the DB, so there's nothing here to edit.
export interface WhatsAppSettings {
  configured: boolean;
  app_id_configured: boolean;
  app_secret_configured: boolean;
  verify_token_configured: boolean;
  access_token_configured: boolean;
  phone_number_id: string | null;
  whatsapp_business_account_id: string | null;
  graph_api_version: string;
  webhook_url: string;
}

// GET /admin/settings/calling — read-only Plivo channel config status.
export interface CallingSettings {
  configured: boolean;
  auth_id_configured: boolean;
  auth_token_configured: boolean;
  phone_number: string | null;
  answer_webhook_url: string;
}

// Calling campaigns — bulk-upload a lead list, the background dialer
// (apps/api/workers/campaign_dialer.py) calls each one, and the AI's
// qualify_lead tool call decides whether a CRM Lead row gets written.
export type CampaignStatus = "running" | "paused" | "completed";
export type CampaignTargetStatus = "pending" | "calling" | "completed" | "failed";

export interface CampaignCounts {
  pending: number;
  calling: number;
  completed: number;
  failed: number;
  qualified: number;
}

export interface Campaign {
  id: string;
  org_id: string;
  name: string;
  criteria: string;
  channel: "voice" | "whatsapp";
  status: CampaignStatus;
  created_at: string;
  counts: CampaignCounts;
}

export interface CampaignTarget {
  id: string;
  campaign_id: string;
  name: string | null;
  phone: string;
  status: CampaignTargetStatus;
  qualified: boolean | null;
  disposition_reason: string | null;
  attempt_count: number;
  conversation_id: string | null;
  created_at: string;
  called_at: string | null;
}

export interface CampaignDetail extends Campaign {
  targets: CampaignTarget[];
}

export interface CampaignCreateResult {
  campaign: Campaign;
  imported: number;
  skipped: number;
  errors: { row: number; reason: string }[];
}

// GET /admin/reports/timeseries — one daily bucket for the reports trend chart.
export interface ReportsTimeseriesPoint {
  date: string;
  calls: number;
  whatsapp_messages: number;
  leads_voice: number;
  leads_whatsapp: number;
  qualified_count: number;
  usd_spend: number;
}

// GET /admin/reports/campaigns — per-campaign conversion row for the reports table.
export interface ReportsCampaignRow {
  id: string;
  name: string;
  channel: "voice" | "whatsapp";
  status: CampaignStatus;
  counts: CampaignCounts;
  qualification_rate: number | null;
}
