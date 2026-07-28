import { redirect } from "next/navigation";

// The Dial screen now lives directly on the AI Calling landing page.
export default function DialRedirectPage() {
  redirect("/calling");
}
