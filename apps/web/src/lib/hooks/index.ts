// Data-fetching hook layer for the Veerox AI admin dashboard (UI plan §6).
// Pages call these hooks instead of touching apiFetch / useEffect directly.

export { useStats } from "./useStats";
export {
  useConversations,
  useConversationMessages,
  type ConversationFilters,
  type ConversationMessagesOptions,
} from "./useConversations";
export {
  useLeads,
  useLead,
  useUpdateLead,
  type LeadFilters,
  type LeadUpdateInput,
} from "./useLeads";
export { useEscalations, type EscalationFilters } from "./useEscalations";
export { useKillSwitch, useSetKillSwitch } from "./useKillSwitch";
export { usePrompts, useTools, useWhatsAppSettings, useCallingSettings } from "./useConfig";
export {
  useOutboundCall,
  useOutboundWhatsApp,
  type OutboundCallInput,
  type OutboundWhatsAppInput,
} from "./useOutbound";
export {
  useCampaigns,
  useCampaign,
  useCreateCampaign,
  usePauseCampaign,
  useResumeCampaign,
  type CreateCampaignInput,
} from "./useCampaigns";
export { useReportsTimeseries, useReportsCampaigns } from "./useReports";
