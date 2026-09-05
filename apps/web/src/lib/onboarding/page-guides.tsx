/**
 * Per-page guided tours. Each entry is an ordered list of steps; every step
 * points at one of the shared anchors that `PageHeader` / `SectionTabs` render
 * on nearly every page:
 *
 *   "header"  → the page title block            (data-tour="page-header")
 *   "action"  → the page's toolbar / action row (data-tour="page-action")
 *   "tabs"    → the in-section sub-navigation   (data-tour="section-tabs")
 *
 * A step whose anchor isn't on the current page is skipped at runtime
 * (`SkipMissingSteps`), so it's safe to include an "action" step on a page
 * that might not have one.
 *
 * `guideForPath()` picks the entry that is the longest prefix of the current
 * path, so `/crm/leads/123` falls back to `/crm/leads`.
 */

export type GuideAnchor = "header" | "action" | "tabs" | "table";
type GuideSide = "top" | "bottom" | "left" | "right";

export interface GuideStep {
  /** A shared anchor (`PageHeader` / `SectionTabs`)… */
  anchor?: GuideAnchor;
  /** …or a page-specific CSS selector (an element `id` or `data-tour`). */
  selector?: string;
  /** Card placement for a `selector` step (defaults to "bottom"). */
  side?: GuideSide;
  title: string;
  content: string;
}

const CONVERSATIONS: GuideStep[] = [
  {
    anchor: "header",
    title: "Conversations",
    content: "A searchable log of every call and chat, across both AI agents.",
  },
  {
    anchor: "action",
    title: "Filter",
    content: "Narrow the list to just calls or just WhatsApp with the channel filter at the top.",
  },
  {
    anchor: "table",
    title: "The columns",
    content:
      "Live shows a pulsing dot while a conversation is still in progress. Client is the person on the other end; Channel is call vs WhatsApp; Started and Ended are the timestamps; # Messages is how many back-and-forth turns it ran.",
  },
  {
    anchor: "table",
    title: "Open one",
    content:
      "Click any row for the full transcript — every message in order — plus the audio recording when it was a call.",
  },
];

const ESCALATIONS: GuideStep[] = [
  {
    anchor: "header",
    title: "Escalations",
    content: "Conversations where the AI decided a human should take over.",
  },
  {
    anchor: "action",
    title: "Filter",
    content: "Show only call escalations or only WhatsApp ones with the channel filter.",
  },
  {
    anchor: "table",
    title: "The columns",
    content:
      "Source and User Phone tell you where it came from and who; Reason is why the AI handed off; Urgency flags how time-sensitive it is; Conversation links to the transcript; Claimed shows which teammate has taken it.",
  },
  {
    anchor: "table",
    title: "Handle one",
    content:
      "Click Claim on a row first, so a teammate doesn't reply at the same time. Then open the Conversation, respond to the customer, and mark it resolved there. Check this queue a few times a day.",
  },
];

const CHANNEL_SETTINGS: GuideStep[] = [
  {
    anchor: "header",
    title: "Agent script",
    content: "The wording this AI agent follows on every conversation.",
  },
  {
    anchor: "header",
    title: "Edit it",
    content:
      "Click Edit, change what the agent says and the questions it asks, then Save script — or Reset to default. The agent can still answer off-script; this just sets the flow it returns to. AI Calling keeps a whole library of scripts; it also has a Voice Provider preference here.",
  },
];

const CHANNEL_LEADS = (channel: string): GuideStep[] => [
  {
    anchor: "header",
    title: `${channel} leads`,
    content: `Contacts the ${channel} agent has qualified, newest first.`,
  },
  {
    anchor: "action",
    title: "Search, filter, import",
    content:
      "Search by intent or tag. Filter by channel, and by pipeline status or review stage. Import Leads adds your own list (use Sample CSV for the column format); Export CSV downloads the current view.",
  },
  {
    anchor: "table",
    title: "The columns",
    content:
      "Name and Phone identify the lead. Intent is what the agent understood they want; Tags are labels it (or you) attached. Status is the sales pipeline — New → Contacted → Qualified → Converted/Lost. Review Stage is your own separate \"is this worth pursuing\" verdict. Created is when the lead came in.",
  },
  {
    anchor: "table",
    title: "Open a lead",
    content:
      "Click any row for the full conversation, notes, and controls to book a follow-up or move the lead along the pipeline.",
  },
];

export const PAGE_GUIDES: Record<string, GuideStep[]> = {
  "/": [
    {
      selector: "#onborda-dashboard-welcome",
      side: "bottom",
      title: "Your home base",
      content: "A live snapshot of both AI agents. Come back here any time from the sidebar.",
    },
    {
      selector: "#onborda-kill-switch",
      side: "bottom",
      title: "The kill switch",
      content:
        "Pause agent stops every AI reply — calls and WhatsApp — instantly. New messages get a short hold reply until you click Resume.",
    },
    {
      selector: "#onborda-stats",
      side: "bottom",
      title: "Today's numbers",
      content: "Conversations, qualified leads, appointments and spend across both channels. Refreshes on its own.",
    },
    {
      selector: '[data-tour="dashboard-shortcuts"]',
      side: "top",
      title: "Jump in",
      content: "Open a channel straight from these cards, or use the sidebar for every other module.",
    },
  ],
  "/calling": [
    {
      anchor: "header",
      title: "AI Calling",
      content: "Your outbound voice agent — it dials, talks, and logs the result.",
    },
    {
      anchor: "action",
      title: "Place a call",
      content:
        "In the Outbound Call card, type a number in +country format, pick the provider if asked, and press Dial Now. The AI agent joins when the person answers.",
    },
    {
      anchor: "tabs",
      title: "The rest of AI Calling",
      content:
        "Use these tabs for call recordings and transcripts (Conversations), qualified Leads, bulk Campaigns, and the agent's script (Settings).",
    },
  ],
  "/calling/conversations": CONVERSATIONS,
  "/calling/escalations": ESCALATIONS,
  "/calling/leads": CHANNEL_LEADS("Calling"),
  "/calling/campaigns": [
    {
      anchor: "header",
      title: "Calling campaigns",
      content: "Call a whole contact list with a script, automatically.",
    },
    {
      anchor: "action",
      title: "Start one",
      content:
        "Create a campaign, pick or upload the contact list, choose the call script and schedule, then Start. Its page then shows live progress and outcomes.",
    },
  ],
  "/calling/settings": CHANNEL_SETTINGS,

  "/whatsapp": [
    {
      anchor: "header",
      title: "AI WhatsApp",
      content: "The WhatsApp agent's inbox — it replies to chats on its own.",
    },
    {
      anchor: "action",
      title: "Send a message",
      content:
        "Use the Send panel: enter the number, type a message or pick an approved template, and Send. Open any chat below to read it or type a reply to take over.",
    },
    {
      anchor: "tabs",
      title: "The rest of AI WhatsApp",
      content:
        "Tabs here cover chat history (Conversations), qualified Leads, and the agent's script (Settings).",
    },
  ],
  "/whatsapp/send": [
    {
      anchor: "header",
      title: "Send a WhatsApp message",
      content: "A one-off outbound message to a single contact.",
    },
    {
      anchor: "action",
      title: "Compose",
      content:
        "Enter the number in full international format. Type the message, or pick an approved template if it's been more than 24 hours since they last replied. Then Send.",
    },
  ],
  "/whatsapp/templates": [
    {
      anchor: "header",
      title: "WhatsApp Templates",
      content:
        "WhatsApp only allows pre-approved templates for messages sent after 24 hours of silence.",
    },
    {
      anchor: "action",
      title: "Add a template",
      content:
        "Click New template, write the text using {{1}}, {{2}} for variables, pick a category, and submit for review.",
    },
    {
      anchor: "table",
      title: "The columns",
      content:
        "Name and Language identify the template; Category is the type WhatsApp filed it under; Params is how many {{n}} variables it has. Status is where it sits in WhatsApp's review — Pending, Approved or Rejected. Active is your own on/off toggle for whether it shows in the send dropdown.",
    },
    {
      anchor: "table",
      title: "Actions",
      content:
        "Use the Actions column to edit or delete a template. Only Approved + Active templates can be picked when sending or in a campaign.",
    },
  ],
  "/whatsapp/conversations": CONVERSATIONS,
  "/whatsapp/escalations": ESCALATIONS,
  "/whatsapp/leads": CHANNEL_LEADS("WhatsApp"),
  "/whatsapp/settings": CHANNEL_SETTINGS,

  "/crm/contacts": [
    {
      anchor: "header",
      title: "Contacts",
      content: "Your master list of every person in the CRM — shared by both AI agents.",
    },
    {
      anchor: "action",
      title: "Search & add",
      content:
        "Search by name, phone or company. Import uploads a CSV (name and phone required); Add contact creates one by hand. Narrow to a segment, then run a campaign against it.",
    },
    {
      anchor: "table",
      title: "The columns",
      content:
        "Name, Phone, Email and Company are the contact's details (blank where the agent hasn't learned them yet); Created is when they were added.",
    },
    {
      anchor: "table",
      title: "Open a contact",
      content:
        "Click any row to see that person's full history — every call and message — plus their tags and custom fields.",
    },
  ],
  "/crm/leads": [
    {
      anchor: "header",
      title: "Leads",
      content: "Contacts an AI agent has qualified, ready for your sales team to work.",
    },
    {
      anchor: "action",
      title: "Search, filter, import",
      content:
        "Search by intent or tag. Filter by channel, and by pipeline status or review stage. Import Leads adds your own list (use Sample CSV for the format); Export CSV downloads the current view.",
    },
    {
      anchor: "table",
      title: "The columns",
      content:
        "Name and Phone identify the lead. Intent is what the agent understood they want; Tags are labels attached to them. Status is the sales pipeline — New → Contacted → Qualified → Converted/Lost. Review Stage is your own separate \"worth pursuing?\" verdict. Created is when it came in.",
    },
    {
      anchor: "table",
      title: "Open a lead",
      content:
        "Click any row for the full conversation, notes, and controls to book a follow-up or move it along the pipeline. Sort by working the freshest ones at the top.",
    },
  ],
  "/crm/appointments": [
    {
      anchor: "header",
      title: "Appointments",
      content: "Every meeting or callback the AI agents have booked.",
    },
    {
      anchor: "action",
      title: "Sort & filter",
      content: "Sort by date and filter by status using the controls at the top.",
    },
    {
      anchor: "table",
      title: "The columns",
      content:
        "Name and Number are who the meeting is with; When and Duration are the slot; Notes hold any context the agent captured. The Status dropdown on each row lets you mark it confirmed, completed or cancelled right from the table.",
    },
  ],
  "/crm/qualification": [
    {
      anchor: "header",
      title: "Qualification",
      content: "How the agent decides a lead is worth pursuing.",
    },
    {
      anchor: "action",
      title: "Tune the rules",
      content:
        "Adjust the qualification questions and criteria here to change what counts as a qualified lead.",
    },
  ],

  "/conversations": CONVERSATIONS,
  "/escalations": ESCALATIONS,
  "/leads": [
    {
      anchor: "header",
      title: "Leads",
      content: "Every qualified lead across both channels, in one list.",
    },
    {
      anchor: "action",
      title: "Search, filter, import",
      content:
        "Search by intent or tag; filter by channel and by pipeline status or review stage. Import Leads adds your own list; Export CSV downloads the current view.",
    },
    {
      anchor: "table",
      title: "The columns",
      content:
        "Name and Phone identify the lead; Intent is what they want; Tags are labels on them. Status is the sales pipeline (New → Contacted → Qualified → Converted/Lost); Review Stage is your own \"worth pursuing?\" check; Created is when it arrived.",
    },
    {
      anchor: "table",
      title: "Open a lead",
      content: "Click any row for the conversation, notes, and controls to book a follow-up or change its status.",
    },
  ],

  "/reports": [
    {
      anchor: "header",
      title: "Reports",
      content: "Volume, qualified-lead rate and cost over time.",
    },
    {
      anchor: "action",
      title: "Read the numbers",
      content:
        "Pick a date range and channel at the top. Use Export to download the figures for your team.",
    },
  ],
  "/analytics/sales": [
    {
      anchor: "header",
      title: "Sales Dashboard",
      content: "A pipeline view for the sales team.",
    },
    {
      anchor: "action",
      title: "Use it",
      content:
        "Leads are grouped by stage and owner. Look for deals with no recent activity and assign someone to chase them.",
    },
  ],

  "/automation/campaigns": [
    {
      anchor: "header",
      title: "Campaigns",
      content:
        "Reach a whole contact list by voice or WhatsApp, automatically. Let's create one — step by step.",
    },
    {
      selector: '[data-tour="campaign-form"]',
      side: "bottom",
      title: "The New campaign form",
      content: "Fill this in top to bottom. We'll go through each field now.",
    },
    {
      selector: "#campaign-name",
      side: "bottom",
      title: "1. Name it",
      content: "Something you'll recognise later in the list below — e.g. \"July outreach\".",
    },
    {
      selector: "#campaign-start-mode",
      side: "bottom",
      title: "2. When to start",
      content:
        "Save as draft to set everything up now and launch later, Start now to begin immediately, or Schedule for… to pick a date and time.",
    },
    {
      selector: "#campaign-file",
      side: "bottom",
      title: "3. Upload the contact list",
      content:
        "A .csv or .xlsx with a phone column (+country code), an optional name column, and call / whatsapp columns (yes/no) that set each contact's channel. Use Sample CSV if you're not sure of the format.",
    },
    {
      selector: "#campaign-criteria",
      side: "top",
      title: "4. Qualification criteria",
      content:
        "Describe what makes a prospect worth pursuing. The AI asks questions against this bar and only the ones it marks interested become CRM leads.",
    },
    {
      selector: "#campaign-script",
      side: "top",
      title: "5. Voice options (optional)",
      content:
        "For calls: choose which number to call from, which script to use, and Call attempts — the most times one contact is ever called (they're re-tried until this cap only while they don't pick up). Leave on Automatic / default if unsure.",
    },
    {
      selector: "#campaign-template",
      side: "top",
      title: "6. WhatsApp template (optional)",
      content:
        "For WhatsApp contacts, pick an approved template so the message sends even outside the 24-hour reply window. Create templates on the WhatsApp Templates page.",
    },
    {
      selector: '[data-tour="campaign-submit"]',
      side: "top",
      title: "7. Launch it",
      content:
        "When everything's set, click here — the button label matches your \"When to start\" choice. The campaign then appears in the table below with live progress.",
    },
    {
      anchor: "action",
      title: "Filter later",
      content: "Use this to show only voice or only WhatsApp campaigns once you have a few running.",
    },
    {
      anchor: "table",
      title: "Your campaigns",
      content:
        "Every campaign you've made. Name and Channel identify it; Status is draft / scheduled / running / paused / completed; Progress is how many contacts have been reached so far; Qualified counts the leads it produced; Created is when you set it up.",
    },
    {
      anchor: "table",
      title: "Run controls",
      content:
        "The Actions column has Start now / Schedule for a draft, and Pause / Resume once it's running. Click any row to open the campaign for the full contact-by-contact breakdown.",
    },
  ],
  "/automation/follow-ups": [
    {
      anchor: "header",
      title: "Automated Follow-up",
      content: "Automatically re-contacts leads that go quiet.",
    },
    {
      anchor: "action",
      title: "Set it up",
      content:
        "Choose how long to wait, how many attempts, and the message or call script for each. Turn it on — leads are nudged on schedule until they reply.",
    },
  ],

  "/team": [
    {
      anchor: "header",
      title: "Team",
      content: "Everyone who can sign in to your organisation, and what each can access.",
    },
    {
      anchor: "action",
      title: "Invite someone",
      content:
        "Click Invite, enter an email, and pick a role. They get their own login link by email.",
    },
    {
      anchor: "table",
      title: "The columns",
      content:
        "Member, Email and Phone are the person's details; Role is Admin (full access) or Member (everything except Team, Settings and Billing); Joined is when they accepted. The Actions column (admins only) lets you change a role or remove access when someone leaves.",
    },
  ],
  "/settings": [
    {
      anchor: "header",
      title: "Settings",
      content:
        "Your account details, and the script each AI agent follows. (Your Veerox team sets up the phone numbers for you.)",
    },
    {
      selector: '[data-tour="settings-channel-tabs"]',
      side: "bottom",
      title: "Pick a channel",
      content: "Switch between the AI Calling and AI WhatsApp agent — each has its own script.",
    },
    {
      anchor: "header",
      title: "Edit the script",
      content:
        "Under Script, click Edit, change the wording of what the agent says and asks, then Save script (or Reset to default). AI Calling also keeps a library of named scripts and lets you set which voice provider to try first.",
    },
  ],
  "/billing": [
    {
      anchor: "header",
      title: "Billing",
      content: "Your plan, credit balance and usage.",
    },
    {
      anchor: "action",
      title: "Manage it",
      content:
        "Top up credits (agents stop at zero), change plan, and review the usage breakdown of what calls and messages cost.",
    },
  ],
  "/billing/upgrade": [
    {
      anchor: "header",
      title: "Upgrade",
      content: "Compare plans and switch.",
    },
    {
      anchor: "action",
      title: "Choose a plan",
      content: "Pick the plan that fits your monthly volume and confirm — new limits apply straight away.",
    },
  ],
  "/support": [
    {
      anchor: "header",
      title: "Support",
      content: "The direct line to the Veerox team.",
    },
    {
      anchor: "action",
      title: "Raise a ticket",
      content: "Click New ticket, describe the problem, attach a screenshot. Replies come by email and show here.",
    },
  ],

  "/support-tickets": [
    {
      anchor: "header",
      title: "Support Tickets",
      content: "The support queue across every organization on the platform.",
    },
    {
      anchor: "action",
      title: "Filter",
      content: "Narrow the queue by status or organization.",
    },
    {
      anchor: "table",
      title: "The columns",
      content:
        "Organization and Raised by say who opened it; Subject and Category summarise the issue; Raised is when; Status is open / in progress / resolved. Click a row to open the ticket, assign it to yourself, reply, and close it.",
    },
  ],
  "/organizations": [
    {
      anchor: "header",
      title: "Organizations",
      content: "Every org on the platform.",
    },
    {
      anchor: "action",
      title: "Find one",
      content: "Search by name to jump to an org.",
    },
    {
      anchor: "table",
      title: "The columns",
      content:
        "Organization, Admin and Email identify the account; Plan and Status show what they're on and whether it's active; Team members is their seat count; Created is signup date. The Actions column opens the org to change its plan, add credits, or review its activity and errors.",
    },
  ],
};

export function guideForPath(pathname: string): GuideStep[] | null {
  const match = Object.keys(PAGE_GUIDES)
    .filter((key) => pathname === key || pathname.startsWith(`${key}/`))
    .sort((a, b) => b.length - a.length)[0];
  return match ? PAGE_GUIDES[match] : null;
}
