import { redirect } from "next/navigation";

// Dial is unambiguously voice-only — send bookmarks straight to its new home
// under the AI Calling Agent section rather than the generic landing page.
export default function DialRedirectPage() {
  redirect("/calling/dial");
}
