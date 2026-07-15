import { redirect } from "next/navigation";

// A deep link to a specific conversation can't know which channel it
// belonged to without a server-side lookup — land on the chooser rather
// than guess wrong. See /whatsapp/conversations/[id] and
// /calling/conversations/[id] for the channel-scoped equivalents.
export default function ConversationRedirectPage() {
  redirect("/");
}
