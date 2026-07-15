"use client";

import { SettingsView } from "@/components/settings/settings-view";

export default function CallingSettingsPage() {
  return (
    <SettingsView
      title="Calling Settings"
      description="Connection status, active prompts, and registered tools for the AI Calling agent."
      promptKeys={["base", "voice_append"]}
      channel="calling"
    />
  );
}
