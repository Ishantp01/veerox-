"use client";

import { SettingsView } from "@/components/settings/settings-view";

export default function WhatsAppSettingsPage() {
  return (
    <SettingsView
      title="WhatsApp Settings"
      description="The script your WhatsApp agent follows."
      channel="whatsapp"
    />
  );
}
