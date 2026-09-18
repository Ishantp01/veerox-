// TypeScript types matching the backend's Pydantic schemas (apps/api/schemas/).
// Keep these in sync with the DB models defined in diagrams.md §5.

export interface Conversation {
  id: string;
  user_id: string;
  channel: "voice" | "whatsapp";
  started_at: string;
  ended_at: string | null;
  message_count?: number;
  // The WhatsApp/voice contact's phone number + name (User.phone / User.name
  // on the backend) — null only for the rare caller/sender the pipeline
  // never resolved to a User row.
  user_phone?: string | null;
  user_name?: string | null;
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

// The 5 built-ins, plus any string — orgs can add their own pipeline stages
// (see LeadStatusPreset below), so this can't be a closed literal union.
// The `string & {}` keeps autocomplete for the built-ins while still
// accepting arbitrary custom status names.
export type LeadStatus =
  | "new"
  | "contacted"
  | "qualified"
  | "converted"
  | "lost"
  | (string & {});

// Separate qualification pipeline from `status` — a lead can be
// status="contacted" while a rep separately works it through this.
export type LeadQualificationStatus = "unqualified" | "in_review" | "qualified" | "disqualified";

export interface Lead {
  id: string;
  org_id: string;
  user_id: string;
  contact_id: string | null;
  conversation_id: string | null;
  name: string | null;
  phone: string | null;
  intent: string | null;
  tags: string[] | null;
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
  claimed_by_account_user_id: string | null;
  claimed_at: string | null;
  claimed_by_name: string | null;
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
  created_by_account_user_id: string | null;
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
  message_template: string | null;
  template_name: string | null;
  template_language: string | null;
  template_params: string[] | null;
  /** Value for the template header's {{1}}, or a resolved public URL for a media header. */
  template_header_params: string[] | null;
  /** Dynamic values for URL/COPY_CODE buttons, by 0-based button position. */
  template_button_params: { index: number; type: "url" | "copy_code"; value: string }[] | null;
  active: boolean;
  created_at: string;
}

export interface TemplateButton {
  type: "QUICK_REPLY" | "URL" | "PHONE_NUMBER" | "COPY_CODE";
  text?: string | null;
  url?: string | null;
  phone_number?: string | null;
  /** URL's {{1}} sample, or the sample code for COPY_CODE. */
  example?: string | null;
}

export interface Template {
  id: string;
  org_id: string;
  name: string;
  language: string;
  category: string | null;
  param_labels: string[];
  body_preview: string | null;
  /** Only TEXT is creatable from the dashboard — IMAGE/VIDEO/DOCUMENT
   * headers need a media handle from Meta's separate upload API. */
  header_type: string | null;
  header_text: string | null;
  header_example: string | null;
  footer_text: string | null;
  buttons: TemplateButton[];
  active: boolean;
  created_at: string;
  /** Live Meta review status (PENDING/APPROVED/REJECTED), matched by
   * name+language against the WABA's templates. Null if Meta couldn't be
   * reached or no matching template exists there yet. */
  meta_status: string | null;
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
  // Not stored on the appointment itself — resolved server-side from
  // whichever of lead_id/contact_id is set.
  name: string | null;
  phone: string | null;
}

// One entry sitting in the Redis human_handoff_queue. Shape produced by
// apps/api/core/tools.py:transfer_to_human.
export interface HandoffQueueEntry {
  reason: string;
  urgency: string;
  user_id: string | null;
  org_id: string;
  channel?: "voice" | "whatsapp" | null;
  phone?: string | null;
  conversation_id?: string | null;
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

// GET/POST/PATCH/DELETE /admin/scripts — one entry in the org's per-channel
// AI script library (see apps/api/db/models/script.py). Pick one per
// campaign, or leave is_default as the fallback (for that channel) every
// campaign without its own pick uses.
export interface ScriptLibraryItem {
  id: string;
  name: string;
  content: string;
  channel: "voice" | "whatsapp";
  is_default: boolean;
  /** Settings-page label only — which org WhatsApp number this script is paired with. */
  phone_number_id: string | null;
  created_at: string;
  updated_at: string;
}

// GET/POST/PATCH/DELETE /admin/qualification-criteria-presets — one entry in
// the org's reusable qualification-criteria library (see
// apps/api/db/models/qualification_criteria_preset.py). Picked from a
// dropdown when creating a campaign instead of retyping the bar every time;
// campaigns already created from a preset keep their own copy of the text.
export interface QualificationCriteriaPreset {
  id: string;
  name: string;
  criteria_text: string;
  created_at: string;
  updated_at: string;
}

// GET/POST/DELETE /admin/lead-status-presets — a custom pipeline stage this
// org added on top of the built-in LeadStatus values (see
// apps/api/db/models/lead_status_preset.py). `name` is the exact string
// written to Lead.status when a lead is set to it.
export interface LeadStatusPreset {
  id: string;
  name: string;
  created_at: string;
}

// GET/POST/PATCH/DELETE /admin/whatsapp-assets — one file in the org's
// WhatsApp media library. The AI agent sends the right one to a contact who
// asks for that information (see apps/api/core/tools.py's send_whatsapp_file).
// Metadata only — the bytes are served from GET /media/wa-asset/{id}.
export interface WhatsAppAsset {
  id: string;
  name: string;
  description: string | null;
  /** "document" | "image" | "video" — the Meta message type. */
  media_type: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  created_at: string;
  updated_at: string;
}

// One of an org's dedicated Plivo/Twilio/WhatsApp numbers — an org can have
// several per provider (see apps/api/db/models/org_phone_number.py).
// Outbound calls dial from whichever row per provider has is_default true;
// for "whatsapp", `phone_number` is Meta's phone_number_id, not an E.164
// number, and is_default picks the fallback number for any send with no
// more specific (campaign-pinned) choice.
export interface OrgPhoneNumber {
  id: string;
  provider: "plivo" | "twilio" | "whatsapp";
  phone_number: string;
  is_default: boolean;
  created_at: string;
}

// GET/PUT /admin/org-numbers — the org's dedicated WhatsApp/calling numbers.
export interface OrgNumbers {
  phone_numbers: OrgPhoneNumber[];
}

// GET/PATCH /billing/platform-settings — platform-admin-only view of the
// help-desk script + social links (superset of GET /billing/social-links).
export interface PlatformSettings {
  help_desk_script: string | null;
  social_links: Record<string, string>;
}

// GET /billing/social-links — read-only for any authenticated org member.
export interface SocialLinks {
  social_links: Record<string, string>;
}

// POST /helpdesk/chat request/response shapes.
export interface HelpDeskMessage {
  role: "user" | "assistant";
  content: string;
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
  channel?: "voice" | "whatsapp" | null;
  conversation_id?: string | null;
  // Only ever set for source: "lead" rows — a raw queue entry has no id to
  // claim until transfer_to_human's Lead write lands (see admin.py).
  claimed_by_account_user_id?: string | null;
  claimed_by_name?: string | null;
  claimed_at?: string | null;
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
  // Chosen template for the human-handoff notification. null = built-in
  // default. The one editable field here (PUT /admin/settings/whatsapp).
  agent_connect_template_name: string | null;
}

// PUT /admin/settings/whatsapp
export interface WhatsAppSettingsInput {
  agent_connect_template_name: string | null;
}

// GET /admin/settings/calling — Plivo channel config status, plus the
// org's own preferred-provider override (the one editable field).
export interface CallingSettings {
  configured: boolean;
  auth_id_configured: boolean;
  auth_token_configured: boolean;
  phone_number: string | null;
  answer_webhook_url: string;
  // Explicit override of the automatic Plivo-first/Twilio-fallback
  // ordering. null = automatic.
  preferred_provider: "plivo" | "twilio" | null;
}

// PUT /admin/settings/calling
export interface CallingSettingsInput {
  preferred_provider: "plivo" | "twilio" | null;
}

// GET/PUT/DELETE /admin/settings/openai-key — the org's own OpenAI key
// (encrypted server-side). The real value is never returned once saved,
// only whether one is configured and a masked preview (e.g. "sk-...ab12").
export interface OpenAIKeySettings {
  configured: boolean;
  key_preview: string | null;
}

// PUT /admin/settings/openai-key
export interface OpenAIKeySettingsInput {
  api_key: string;
}

// GET/PUT/DELETE /admin/settings/plivo-credentials — this org's own Plivo
// account. No platform-wide fallback: Plivo is unavailable for this org
// until these are set.
export interface PlivoCredentialsSettings {
  configured: boolean;
  auth_id: string | null;
  auth_token_preview: string | null;
}

// PUT /admin/settings/plivo-credentials
export interface PlivoCredentialsSettingsInput {
  auth_id: string;
  auth_token: string;
}

// GET/PUT/DELETE /admin/settings/twilio-credentials — this org's own Twilio
// account. No platform-wide fallback.
export interface TwilioCredentialsSettings {
  configured: boolean;
  account_sid: string | null;
  auth_token_preview: string | null;
}

// PUT /admin/settings/twilio-credentials
export interface TwilioCredentialsSettingsInput {
  account_sid: string;
  auth_token: string;
}

// GET/PUT/DELETE /admin/settings/meta-credentials — this org's own Meta
// WhatsApp App. No platform-wide fallback.
export interface MetaCredentialsSettings {
  configured: boolean;
  app_id: string | null;
  app_secret_configured: boolean;
  app_secret_preview: string | null;
  access_token_configured: boolean;
  access_token_preview: string | null;
  business_account_id: string | null;
  verify_token_configured: boolean;
  verify_token_preview: string | null;
}

// PUT /admin/settings/meta-credentials
export interface MetaCredentialsSettingsInput {
  app_id: string;
  app_secret: string;
  access_token: string;
  business_account_id?: string | null;
  verify_token?: string | null;
}

// Calling campaigns — bulk-upload a lead list, the background dialer
// (apps/api/workers/campaign_dialer.py) calls each one, and the AI's
// qualify_lead tool call decides whether a CRM Lead row gets written.
export type CampaignStatus = "draft" | "scheduled" | "running" | "paused" | "completed";
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
  /** Display-only summary of this campaign's target channels — "mixed" when
   * it has both. Routing is per-target, see CampaignTarget.channel. */
  channel: "voice" | "whatsapp" | "mixed";
  status: CampaignStatus;
  scheduled_start_at: string | null;
  template_name: string | null;
  template_language: string | null;
  template_params: string[] | null;
  /** Same convention as template_params, but for the template's HEADER
   * placeholder (a {{1}} value, or the resolved URL of a media header) —
   * see template-param-mapper.tsx. */
  template_header_params: string[] | null;
  /** Dynamic values for URL/COPY_CODE buttons, by 0-based button position. */
  template_button_params: { index: number; type: "url" | "copy_code"; value: string }[] | null;
  custom_message: string | null;
  // Voice-only overrides — null means "use the org default script" /
  // "auto-rotate across the org's numbers".
  script_id: string | null;
  phone_number_id: string | null;
  // WhatsApp-only siblings of the two above — null means "use the org
  // default WhatsApp script" / "send from the org's default WhatsApp number".
  whatsapp_script_id: string | null;
  whatsapp_number_id: string | null;
  // Voice-only: how many times the dialer re-calls a target that never
  // connects before marking it failed (any integer >= 1, default 3).
  max_attempts: number;
  /** The teammate who created this campaign. A "member" only ever sees their
   * own campaigns; admins see all, with `created_by_name` for the column. */
  created_by_account_user_id: string | null;
  created_by_name: string | null;
  created_at: string;
  counts: CampaignCounts;
}

export interface CampaignTarget {
  id: string;
  campaign_id: string;
  name: string | null;
  phone: string;
  channel: "voice" | "whatsapp";
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
  campaigns: Campaign[];
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
  channel: "voice" | "whatsapp" | "mixed";
  status: CampaignStatus;
  counts: CampaignCounts;
  qualification_rate: number | null;
}
