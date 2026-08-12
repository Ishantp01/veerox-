// Data-fetching hook layer for the Veerox AI admin dashboard (UI plan §6).
// Pages call these hooks instead of touching apiFetch / useEffect directly.

export { useStats } from "./useStats";
export {
  useTeamMembers,
  useInviteMember,
  useUpdateMemberRole,
  useRemoveMember,
  type TeamMember,
  type InviteMemberInput,
  type InviteMemberResult,
} from "./useTeam";
export {
  useConversations,
  useConversationMessages,
  useSummarizeConversation,
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
export { useWhatsAppSettings, useCallingSettings } from "./useConfig";
export { useScript, useUpdateScript } from "./useScript";
export { useOrgNumbers, useUpdateOrgNumbers } from "./useOrgNumbers";
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
export {
  useContacts,
  useContact,
  useCreateContact,
  type ContactCreateInput,
} from "./useContacts";
export {
  useAppointments,
  useCreateAppointment,
  useUpdateAppointment,
  type AppointmentFilters,
  type AppointmentCreateInput,
  type AppointmentUpdateInput,
} from "./useAppointments";
export { usePipeline, useRevenueSummary } from "./useSales";
export {
  useFollowUpRules,
  useCreateFollowUpRule,
  useUpdateFollowUpRule,
  useFollowUpTasks,
  useCancelFollowUpTask,
  type FollowUpRuleCreateInput,
  type FollowUpTaskFilters,
} from "./useFollowUps";
export {
  useTemplates,
  useCreateTemplate,
  useUpdateTemplate,
  useDeleteTemplate,
  type TemplateFilters,
  type TemplateCreateInput,
  type TemplateUpdateInput,
} from "./useTemplates";
export {
  useBillingStatus,
  useBillingUsage,
  useAvailablePlans,
  useCreateCheckoutSession,
  useVerifyPayment,
  type Plan,
  type BillingStatus,
  type BillingUsage,
  type UsageMetric,
  type CheckoutSession,
  type VerifyPaymentInput,
} from "./useBilling";
export {
  useAdminPlans,
  useCreatePlan,
  useUpdatePlan,
  useDeletePlan,
  type AdminPlan,
  type CreatePlanInput,
  type UpdatePlanInput,
} from "./useAdminPlans";
export { useAdminOrgs, type AdminOrg } from "./useAdminOrgs";
export {
  usePlatformSettings,
  useUpdatePlatformSettings,
  type UpdatePlatformSettingsInput,
} from "./useAdminSettings";
export { useSocialLinks } from "./useSocialLinks";
export { useHelpDeskChat, type HelpDeskChatInput, type HelpDeskChatResult } from "./useHelpDesk";
