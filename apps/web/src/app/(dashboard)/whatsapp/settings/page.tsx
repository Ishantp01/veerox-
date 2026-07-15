"use client";

import { SettingsView } from "@/components/settings/settings-view";

export default function WhatsAppSettingsPage() {
  return (
    <SettingsView
      title="WhatsApp Settings"
      description="Connection status, active prompts, and registered tools for the WhatsApp agent."
      promptKeys={["base", "whatsapp_append"]}
      channel="whatsapp"
    />
  );
}
