import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  CallingSettings,
  CallingSettingsInput,
  MetaCredentialsSettings,
  MetaCredentialsSettingsInput,
  OpenAIKeySettings,
  OpenAIKeySettingsInput,
  PlivoCredentialsSettings,
  PlivoCredentialsSettingsInput,
  SocialLinksSettings,
  SocialLinksSettingsInput,
  TwilioCredentialsSettings,
  TwilioCredentialsSettingsInput,
  WhatsAppSettings,
  WhatsAppSettingsInput,
} from "@/lib/types";

/**
 * Read-only WhatsApp/Meta channel config status (masked secrets). Static
 * config — no polling; relies on the default 30s staleTime.
 *
 * GET /admin/settings/whatsapp → WhatsAppSettings
 */
export function useWhatsAppSettings() {
  return useQuery<WhatsAppSettings>({
    queryKey: queryKeys.whatsappSettings(),
    queryFn: () => apiFetch<WhatsAppSettings>("/admin/settings/whatsapp"),
  });
}

/**
 * Set (or, with `agent_connect_template_name: null`, clear back to the
 * built-in default) which approved template the human-handoff notification
 * sends — see apps/api/core/tools.py::transfer_to_human.
 *
 * PUT /admin/settings/whatsapp → WhatsAppSettings
 */
export function useUpdateWhatsAppSettings() {
  const queryClient = useQueryClient();
  return useMutation<WhatsAppSettings, Error, WhatsAppSettingsInput>({
    mutationFn: (body) =>
      apiFetch<WhatsAppSettings>("/admin/settings/whatsapp", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.whatsappSettings(), data);
    },
  });
}

/**
 * This org's social/contact links. Static config — no polling; relies on
 * the default 30s staleTime.
 *
 * GET /admin/settings/social-links → SocialLinksSettings
 */
export function useSocialLinksSettings() {
  return useQuery<SocialLinksSettings>({
    queryKey: queryKeys.socialLinksSettings(),
    queryFn: () => apiFetch<SocialLinksSettings>("/admin/settings/social-links"),
  });
}

/**
 * Replace this org's social/contact links. The WhatsApp/voice agent then
 * shares them automatically when a contact asks — no script edits needed.
 *
 * PUT /admin/settings/social-links → SocialLinksSettings
 */
export function useUpdateSocialLinksSettings() {
  const queryClient = useQueryClient();
  return useMutation<SocialLinksSettings, Error, SocialLinksSettingsInput>({
    mutationFn: (body) =>
      apiFetch<SocialLinksSettings>("/admin/settings/social-links", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.socialLinksSettings(), data);
    },
  });
}

/**
 * Read-only Plivo voice channel config status (masked secrets). Static
 * config — no polling; relies on the default 30s staleTime.
 *
 * GET /admin/settings/calling → CallingSettings
 */
export function useCallingSettings() {
  return useQuery<CallingSettings>({
    queryKey: queryKeys.callingSettings(),
    queryFn: () => apiFetch<CallingSettings>("/admin/settings/calling"),
  });
}

/**
 * Set (or, with `preferred_provider: null`, clear back to automatic) this
 * org's preferred voice provider — applied to every outbound call the org
 * places (single admin call, AI callback, campaign dialer, follow-up
 * dispatcher).
 *
 * PUT /admin/settings/calling → CallingSettings
 */
export function useUpdateCallingSettings() {
  const queryClient = useQueryClient();
  return useMutation<CallingSettings, Error, CallingSettingsInput>({
    mutationFn: (body) =>
      apiFetch<CallingSettings>("/admin/settings/calling", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.callingSettings(), data);
    },
  });
}

/**
 * Read-only status of this org's own OpenAI key — `configured` plus a
 * masked preview (e.g. "sk-...ab12"). The real key is never returned once
 * saved. Static config — no polling; relies on the default 30s staleTime.
 *
 * GET /admin/settings/openai-key → OpenAIKeySettings
 */
export function useOpenAIKeySettings() {
  return useQuery<OpenAIKeySettings>({
    queryKey: queryKeys.openaiKeySettings(),
    queryFn: () => apiFetch<OpenAIKeySettings>("/admin/settings/openai-key"),
  });
}

/**
 * Set this org's own OpenAI key — every call (chat, transcription, voice)
 * for this org then bills against it instead of the platform's shared key.
 *
 * PUT /admin/settings/openai-key → OpenAIKeySettings
 */
export function useUpdateOpenAIKeySettings() {
  const queryClient = useQueryClient();
  return useMutation<OpenAIKeySettings, Error, OpenAIKeySettingsInput>({
    mutationFn: (body) =>
      apiFetch<OpenAIKeySettings>("/admin/settings/openai-key", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.openaiKeySettings(), data);
    },
  });
}

/**
 * Clear this org's own OpenAI key, falling back to the platform key.
 *
 * DELETE /admin/settings/openai-key → OpenAIKeySettings
 */
export function useDeleteOpenAIKeySettings() {
  const queryClient = useQueryClient();
  return useMutation<OpenAIKeySettings, Error, void>({
    mutationFn: () =>
      apiFetch<OpenAIKeySettings>("/admin/settings/openai-key", {
        method: "DELETE",
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.openaiKeySettings(), data);
    },
  });
}

/**
 * Read-only status of this org's own Plivo account — `configured` plus a
 * masked auth-token preview. No platform-wide fallback: Plivo is
 * unavailable for this org until these are set.
 *
 * GET /admin/settings/plivo-credentials → PlivoCredentialsSettings
 */
export function usePlivoCredentialsSettings() {
  return useQuery<PlivoCredentialsSettings>({
    queryKey: queryKeys.plivoCredentialsSettings(),
    queryFn: () => apiFetch<PlivoCredentialsSettings>("/admin/settings/plivo-credentials"),
  });
}

/** PUT /admin/settings/plivo-credentials → PlivoCredentialsSettings */
export function useUpdatePlivoCredentialsSettings() {
  const queryClient = useQueryClient();
  return useMutation<PlivoCredentialsSettings, Error, PlivoCredentialsSettingsInput>({
    mutationFn: (body) =>
      apiFetch<PlivoCredentialsSettings>("/admin/settings/plivo-credentials", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.plivoCredentialsSettings(), data);
    },
  });
}

/** DELETE /admin/settings/plivo-credentials → PlivoCredentialsSettings */
export function useDeletePlivoCredentialsSettings() {
  const queryClient = useQueryClient();
  return useMutation<PlivoCredentialsSettings, Error, void>({
    mutationFn: () =>
      apiFetch<PlivoCredentialsSettings>("/admin/settings/plivo-credentials", {
        method: "DELETE",
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.plivoCredentialsSettings(), data);
    },
  });
}

/**
 * Read-only status of this org's own Twilio account. No platform-wide
 * fallback.
 *
 * GET /admin/settings/twilio-credentials → TwilioCredentialsSettings
 */
export function useTwilioCredentialsSettings() {
  return useQuery<TwilioCredentialsSettings>({
    queryKey: queryKeys.twilioCredentialsSettings(),
    queryFn: () => apiFetch<TwilioCredentialsSettings>("/admin/settings/twilio-credentials"),
  });
}

/** PUT /admin/settings/twilio-credentials → TwilioCredentialsSettings */
export function useUpdateTwilioCredentialsSettings() {
  const queryClient = useQueryClient();
  return useMutation<TwilioCredentialsSettings, Error, TwilioCredentialsSettingsInput>({
    mutationFn: (body) =>
      apiFetch<TwilioCredentialsSettings>("/admin/settings/twilio-credentials", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.twilioCredentialsSettings(), data);
    },
  });
}

/** DELETE /admin/settings/twilio-credentials → TwilioCredentialsSettings */
export function useDeleteTwilioCredentialsSettings() {
  const queryClient = useQueryClient();
  return useMutation<TwilioCredentialsSettings, Error, void>({
    mutationFn: () =>
      apiFetch<TwilioCredentialsSettings>("/admin/settings/twilio-credentials", {
        method: "DELETE",
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.twilioCredentialsSettings(), data);
    },
  });
}

/**
 * Read-only status of this org's own Meta WhatsApp App. No platform-wide
 * fallback.
 *
 * GET /admin/settings/meta-credentials → MetaCredentialsSettings
 */
export function useMetaCredentialsSettings() {
  return useQuery<MetaCredentialsSettings>({
    queryKey: queryKeys.metaCredentialsSettings(),
    queryFn: () => apiFetch<MetaCredentialsSettings>("/admin/settings/meta-credentials"),
  });
}

/** PUT /admin/settings/meta-credentials → MetaCredentialsSettings */
export function useUpdateMetaCredentialsSettings() {
  const queryClient = useQueryClient();
  return useMutation<MetaCredentialsSettings, Error, MetaCredentialsSettingsInput>({
    mutationFn: (body) =>
      apiFetch<MetaCredentialsSettings>("/admin/settings/meta-credentials", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.metaCredentialsSettings(), data);
    },
  });
}

/** DELETE /admin/settings/meta-credentials → MetaCredentialsSettings */
export function useDeleteMetaCredentialsSettings() {
  const queryClient = useQueryClient();
  return useMutation<MetaCredentialsSettings, Error, void>({
    mutationFn: () =>
      apiFetch<MetaCredentialsSettings>("/admin/settings/meta-credentials", {
        method: "DELETE",
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.metaCredentialsSettings(), data);
    },
  });
}
