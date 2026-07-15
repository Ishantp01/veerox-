import { redirect } from "next/navigation";

// Conversations are now split by channel under /whatsapp and /calling. A
// bookmark to this unified list can't know which channel was meant, so land
// on the chooser rather than guess.
export default function ConversationsRedirectPage() {
  redirect("/");
}
