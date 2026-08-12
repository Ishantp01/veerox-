"use client";

import { SettingsView } from "@/components/settings/settings-view";

export default function CallingSettingsPage() {
  return (
    <SettingsView
      title="Calling Settings"
      description="The script your AI Calling agent follows."
      channel="calling"
    />
  );
}
