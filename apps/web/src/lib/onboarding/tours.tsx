import type { OnbordaProps } from "onborda";
import {
  LayoutDashboard,
  Power,
  BarChart3,
  TrendingUp,
  Phone,
  MessageSquare,
  FileText,
  Users,
  UserCheck,
  CalendarClock,
  MessagesSquare,
  AlertTriangle,
  Megaphone,
  Repeat,
  UsersRound,
  Settings,
  CreditCard,
  LifeBuoy,
  Building2,
} from "lucide-react";

/** A single Onborda tour ({ tour, steps }). `Tour`/`Step` aren't re-exported
 *  from the package index, so derive them from the `Onborda` component props. */
type Tour = OnbordaProps["steps"][number];
type Step = Tour["steps"][number];

/** Tour name passed to `startOnborda()`. */
export const TOUR_NEW_ACCOUNT = "veerox-intro";

/**
 * Anchor element ids for steps that point at the dashboard landing page
 * (everything else points at a sidebar link — see `ONBORDA_NAV_IDS`).
 */
export const ONBORDA_IDS = {
  dashboardWelcome: "onborda-dashboard-welcome",
  killSwitch: "onborda-kill-switch",
  stats: "onborda-stats",
} as const;

/**
 * Sidebar nav `href` → anchor id. `components/nav.tsx` stamps the id onto the
 * matching `<Link>`. Covers every module in the sidebar; links the current
 * account can't see (plan-gated, role-gated) simply won't render, and
 * `buildTour()` drops their steps at runtime.
 */
export const ONBORDA_NAV_IDS: Record<string, string> = {
  "/": "onborda-nav-dashboard",
  "/calling": "onborda-nav-calling",
  "/whatsapp": "onborda-nav-whatsapp",
  "/whatsapp/templates": "onborda-nav-templates",
  "/crm/contacts": "onborda-nav-contacts",
  "/crm/leads": "onborda-nav-leads",
  "/crm/appointments": "onborda-nav-appointments",
  "/conversations": "onborda-nav-conversations",
  "/escalations": "onborda-nav-escalations",
  "/reports": "onborda-nav-reports",
  "/analytics/sales": "onborda-nav-sales",
  "/automation/campaigns": "onborda-nav-campaigns",
  "/automation/follow-ups": "onborda-nav-followups",
  "/team": "onborda-nav-team",
  "/settings": "onborda-nav-settings",
  "/billing": "onborda-nav-billing",
  "/support": "onborda-nav-support",
  // Platform group — only present for Veerox staff / superuser sessions.
  "/organizations": "onborda-nav-organizations",
  "/support-tickets": "onborda-nav-support-tickets",
};

const ic = (Cmp: typeof LayoutDashboard) => <Cmp className="h-4 w-4" aria-hidden />;

/**
 * Step body: one line on what the module is for, then a "How to use it" block
 * so a new user can operate it without anyone explaining. `how` is one or more
 * short imperative sentences.
 */
function body(what: string, how: string): React.ReactNode {
  return (
    <>
      <p>{what}</p>
      <p className="mt-2 font-medium text-slate-700 dark:text-slate-200">How to use it</p>
      <p className="mt-0.5 text-slate-500 dark:text-slate-400">{how}</p>
    </>
  );
}

const navStep = (
  id: string,
  Cmp: typeof LayoutDashboard,
  title: string,
  what: string,
  how: string,
): Step => ({
  icon: ic(Cmp),
  title,
  content: body(what, how),
  selector: `#${id}`,
  side: "right",
  pointerPadding: 6,
  pointerRadius: 10,
  showControls: true,
});

/**
 * The full new-account walkthrough — one step per module, each explaining how
 * to actually use that module. `buildTour()` filters this down to the modules
 * the current account can see before the tour starts, so plan-gated /
 * role-gated links never produce a broken step.
 */
export const ALL_STEPS: Step[] = [
  {
    icon: ic(LayoutDashboard),
    title: "Welcome to Veerox",
    content: body(
      "This is your command center — a live view of your AI calling and WhatsApp agents.",
      "Use the sidebar on the left to move between modules. This walkthrough covers every one; click Next to continue, or the × to leave any time.",
    ),
    selector: `#${ONBORDA_IDS.dashboardWelcome}`,
    side: "bottom",
    pointerPadding: 10,
    pointerRadius: 12,
    showControls: true,
  },
  {
    icon: ic(Power),
    title: "Master kill switch",
    content: body(
      "One switch pauses every AI agent at once — calls and WhatsApp both.",
      "Click Pause agent to stop all automated replies; new messages get a short hold message until you click Resume. Use it during a script change or out of hours.",
    ),
    selector: `#${ONBORDA_IDS.killSwitch}`,
    side: "bottom",
    pointerPadding: 10,
    pointerRadius: 12,
    showControls: true,
  },
  {
    icon: ic(BarChart3),
    title: "Live numbers",
    content: body(
      "Today's conversations, qualified leads, appointments, spend and errors across both channels.",
      "These refresh on their own — no need to reload. Open a channel or Reports for the full breakdown behind each number.",
    ),
    selector: `#${ONBORDA_IDS.stats}`,
    side: "top",
    pointerPadding: 10,
    pointerRadius: 12,
    showControls: true,
  },

  navStep(
    "onborda-nav-dashboard",
    LayoutDashboard,
    "Dashboard",
    "The overview screen you're looking at now.",
    "Click Dashboard from anywhere to return here for the at-a-glance status of both agents.",
  ),

  // Communication
  navStep(
    "onborda-nav-calling",
    Phone,
    "AI Calling",
    "Your outbound voice agent — it places calls, talks to people and logs the outcome.",
    "Open it, then use Dial to place a call, Conversations to hear recordings and read transcripts, and Calling → Settings to edit the agent's script, voice and phone number.",
  ),
  navStep(
    "onborda-nav-whatsapp",
    MessageSquare,
    "AI WhatsApp",
    "The WhatsApp agent's inbox — it replies to chats automatically.",
    "Open any chat to read it or take over and type yourself. Use Send for a one-off message, and WhatsApp → Settings to set the agent's instructions and working hours.",
  ),
  navStep(
    "onborda-nav-templates",
    FileText,
    "WhatsApp Templates",
    "WhatsApp only allows pre-approved templates for messages sent after 24 hours of silence.",
    "Create a template, submit it for WhatsApp's approval (usually minutes to a few hours), then choose it when sending a campaign or outbound message.",
  ),

  // CRM
  navStep(
    "onborda-nav-contacts",
    Users,
    "Contacts",
    "Your master list of every person — the agents add new people here automatically.",
    "Click Import to upload a CSV, or Add contact for one person. Open a contact to see every call and message with them, plus tags and custom fields.",
  ),
  navStep(
    "onborda-nav-leads",
    UserCheck,
    "Leads",
    "Contacts an agent has qualified, each with a status and score.",
    "Filter by status to work your pipeline. Open a lead to read the conversation, book a follow-up, or mark it Won / Lost.",
  ),
  navStep(
    "onborda-nav-appointments",
    CalendarClock,
    "Appointments",
    "Every meeting or callback the agents have booked.",
    "Switch between day and list views; click an appointment to see details, reschedule or cancel. They sync to the calendar you connect in Settings.",
  ),
  navStep(
    "onborda-nav-conversations",
    MessagesSquare,
    "Conversations",
    "A searchable archive of every call and chat across both channels.",
    "Search by name or number, filter by channel or date, and open any item for the full transcript and recording.",
  ),
  navStep(
    "onborda-nav-escalations",
    AlertTriangle,
    "Escalations",
    "Conversations the agent decided a human should handle.",
    "Click one to claim it so a teammate doesn't double-reply, respond to the customer, then mark it Resolved. Check it a few times a day.",
  ),

  // Analytics
  navStep(
    "onborda-nav-reports",
    BarChart3,
    "Reports",
    "Volume, qualified-lead rate and cost over time, and per-campaign results.",
    "Pick a date range and channel at the top. Use the export button to hand figures to your team.",
  ),
  navStep(
    "onborda-nav-sales",
    TrendingUp,
    "Sales Dashboard",
    "A pipeline-focused view built for the sales team.",
    "See leads grouped by stage and owner, and spot deals that have gone quiet so someone can chase them.",
  ),

  // Automation
  navStep(
    "onborda-nav-campaigns",
    Megaphone,
    "Campaigns",
    "Bulk outreach — call or message a whole contact list with a script sequence.",
    "Click New campaign, pick or upload a contact list, choose the script(s) and a schedule, then Start. Progress and results update on the campaign's page while it runs.",
  ),
  navStep(
    "onborda-nav-followups",
    Repeat,
    "Automated Follow-up",
    "Automatically re-contacts leads that stop replying.",
    "Set the wait time, how many attempts, and the message or call script. Each lead is nudged on schedule until they respond or you turn it off.",
  ),

  // Settings
  navStep(
    "onborda-nav-team",
    UsersRound,
    "Team",
    "Your teammates and what each of them can access.",
    "Click Invite, enter an email, and choose Admin (full access) or Member (no billing or settings). Remove someone's access from the same list when they leave.",
  ),
  navStep(
    "onborda-nav-settings",
    Settings,
    "Settings — start here",
    "Where you connect your channels. Nothing runs until this is done.",
    "Add your WhatsApp Business number and your calling provider's credentials, connect your calendar, then send yourself a test call and message. Do this before anything else.",
  ),
  navStep(
    "onborda-nav-billing",
    CreditCard,
    "Billing",
    "Your plan, usage and credit balance.",
    "Top up credits, change plan, and review a breakdown of what calls and messages cost. Agents stop when credits hit zero, so keep some balance.",
  ),
  navStep(
    "onborda-nav-support",
    LifeBuoy,
    "Support",
    "The direct line to the Veerox team.",
    "Click New ticket, describe the problem and attach a screenshot. Replies arrive by email and show up on the ticket here.",
  ),

  // Platform group — rendered only for Veerox staff / superuser sessions, so
  // `buildTour()` drops these for a normal customer account.
  navStep(
    "onborda-nav-organizations",
    Building2,
    "Organizations",
    "Every org on the platform, with its plan, billing and token usage.",
    "Open an org to change its plan, add credits, or review its activity and errors.",
  ),
  navStep(
    "onborda-nav-support-tickets",
    LifeBuoy,
    "Support Tickets",
    "The support queue across every organization.",
    "Assign a ticket to yourself, reply to the customer, and close it when it's resolved.",
  ),
];

/**
 * Build the tour for the account viewing it: keep only steps whose anchor is
 * actually in the DOM. Call this immediately before `startOnborda()` so the
 * sidebar (which gates links by plan + role) has already rendered.
 */
export function buildTour(): Tour[] {
  const steps =
    typeof document === "undefined"
      ? ALL_STEPS
      : ALL_STEPS.filter((step) => document.querySelector(step.selector) !== null);
  return [{ tour: TOUR_NEW_ACCOUNT, steps }];
}
