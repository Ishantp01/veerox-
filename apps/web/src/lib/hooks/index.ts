// Data-fetching hook layer for the Veerox AI admin dashboard (UI plan §6).
// Pages call these hooks instead of touching apiFetch / useEffect directly.

export { useStats } from "./useStats";
export {
  useTeamMembers,
  useInviteMember,
  useUpdateMember,
  useRemoveMember,
  type TeamMember,
  type InviteMemberInput,
  type InviteMemberResult,
  type UpdateMemberInput,
} from "./useTeam";
export {
  useConversations,
  useConversationMessages,
  useSummarizeConversation,
  useUpdateConversation,
  findConversationByPhone,
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
export {
  useLeadFollowUps,
  useUpdateLeadFollowUp,
  followUpKey,
  type LeadFollowUp,
} from "./useLeadFollowUps";
export { useHumanSupport, useClaimHumanSupport, useRequestHumanSupport, useLeadHumanSupport, type HumanSupportFilters } from "./useHumanSupport";
export { useKillSwitch, useSetKillSwitch } from "./useKillSwitch";
export {
  useWhatsAppSettings,
  useUpdateWhatsAppSettings,
  useCallingSettings,
  useUpdateCallingSettings,
  useCountryCodeSettings,
  useUpdateCountryCodeSettings,
  useOpenAIKeySettings,
  useUpdateOpenAIKeySettings,
  useDeleteOpenAIKeySettings,
  usePlivoCredentialsSettings,
  useUpdatePlivoCredentialsSettings,
  useDeletePlivoCredentialsSettings,
  useTwilioCredentialsSettings,
  useUpdateTwilioCredentialsSettings,
  useDeleteTwilioCredentialsSettings,
  useMetaCredentialsSettings,
  useUpdateMetaCredentialsSettings,
  useDeleteMetaCredentialsSettings,
} from "./useConfig";
export {
  useScripts,
  useCreateScript,
  useUpdateScriptLibraryItem,
  useSetDefaultScript,
  useDeleteScript,
  type ScriptChannel,
  type ScriptCreateInput,
  type ScriptLibraryUpdateInput,
} from "./useScripts";
export {
  useQualificationCriteriaPresets,
  useCreateQualificationCriteriaPreset,
  useUpdateQualificationCriteriaPreset,
  useDeleteQualificationCriteriaPreset,
  type QualificationCriteriaPresetCreateInput,
  type QualificationCriteriaPresetUpdateInput,
} from "./useQualificationCriteriaPresets";
export {
  useLeadStatusPresets,
  useCreateLeadStatusPreset,
  useDeleteLeadStatusPreset,
} from "./useLeadStatusPresets";
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
  useRetryCampaignTarget,
  useScheduleCampaign,
  useUpdateCampaign,
  type CreateCampaignInput,
  type CampaignStartMode,
} from "./useCampaigns";
export { useReportsTimeseries, useReportsCampaigns } from "./useReports";
export {
  useContacts,
  useContact,
  useCreateContact,
  useCreateLeadFromContact,
  useUpdateContact,
  useDeleteContact,
  type ContactCreateInput,
  type ContactUpdateInput,
} from "./useContacts";
export {
  useAppointments,
  useCreateAppointment,
  useUpdateAppointment,
  useDeleteAppointment,
  type AppointmentFilters,
  type AppointmentSort,
  type AppointmentCreateInput,
  type AppointmentUpdateInput,
} from "./useAppointments";
export { usePipeline, useRevenueSummary } from "./useSales";
export {
  useFollowUpRules,
  useCreateFollowUpRule,
  useUpdateFollowUpRule,
  useDeleteFollowUpRule,
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
  useSyncTemplates,
  type TemplateFilters,
  type TemplateCreateInput,
  type TemplateUpdateInput,
  type TemplateSyncResult,
} from "./useTemplates";
export {
  useWhatsappAssets,
  useUploadWhatsappAsset,
  useUpdateWhatsappAsset,
  useDeleteWhatsappAsset,
  type UploadWhatsappAssetInput,
  type UpdateWhatsappAssetInput,
} from "./useWhatsappAssets";
export {
  useAdminOrgs,
  useIssueLicense,
  useRenewLicense,
  useExtendLicense,
  useSuspendLicense,
  useReactivateLicense,
  type AdminOrg,
} from "./useAdminOrgs";
export {
  usePlatformSettings,
  useUpdatePlatformSettings,
  type UpdatePlatformSettingsInput,
} from "./useAdminSettings";
export { useSocialLinks } from "./useSocialLinks";
export { useHelpDeskChat, type HelpDeskChatInput, type HelpDeskChatResult } from "./useHelpDesk";
export { useClientPagination, type ClientPagination } from "./useClientPagination";
export { useOrgCountryCode } from "./useOrgCountryCode";
