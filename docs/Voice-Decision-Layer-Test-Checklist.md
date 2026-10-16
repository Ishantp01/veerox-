# Voice Decision Layer — real-call test checklist

The decision logic is covered by automated tests and a real-model check, but nothing replaces real calls. Do these on a **test org with its own phone number**, never on an org whose number real customers call.

## Switches (all default to the safe side)

| Switch | Where | Meaning |
|---|---|---|
| `CONVERSATION_VOICE_LIVE` (env, default `true`) | box `.env` | Global ceiling. `false` stops live voice decisions everywhere without a deploy (restart the api container). |
| Organization -> Conversation behaviour | Organizations -> Edit | **Human Conversation** or **Smart Script**. |
| Organization -> Voice calls | Organizations -> Edit | **Off** = nothing, **Log only** (default) = records what it would do, **Live** = steers the call. |
| `CONVERSATION_VOICE_DECISION_BUDGET_SECONDS` (default 1.0) / `CONVERSATION_VOICE_LLM_TIMEOUT_SECONDS` (default 0.9) | box `.env` | How long a reply waits for a decision before the call just continues as today. |

A call is steered only when all three are on. Any error, timeout or low confidence leaves the call exactly as it was.

## Stage 1 — Log only (2–3 days of real calls)

Set the test org to Human Conversation + **Log only**. Place the calls below. After each, open **Conversation Engine -> Turn log**, filter **Shadow (voice)**.

Pass when: one row per caller turn, correct scenario/confidence, **no action executed**, call quality unchanged (no added delay, no dropped audio).

## Stage 2 — Live (test org only)

Set the test org's Voice calls to **Live**. Repeat the calls. Filter the turn log by **Live**; voice rows show the instruction given to the speaking model.

| # | Caller says | Expected decision | Expected on the call |
|---|---|---|---|
| 1 | "Haan bataiye, main abhi busy hoon" | busy / callback, asks a time | Agent stops the pitch, asks when to call back |
| 2 | "Shaam ko 6 baje" (after #1) | `request_callback` runs once | Agent confirms 6 pm; one callback under Appointments |
| 3 | "Premium plan ka price kya hai" | price, approved knowledge | Agent states only the approved price |
| 4 | "Bahut mehenga hai" | objection | Agent acknowledges, offers a call, no arguing |
| 5 | A question with no approved answer | unknown -> `transfer_to_human` | Agent says the team will confirm; nothing invented |
| 6 | "I want to book a demo tomorrow at 4 pm" | `book_appointment` runs once | Agent confirms; one appointment |
| 7 | "Send the details on WhatsApp" | `send_whatsapp_message` (approved text only) | Agent says details were sent; message arrives |
| 8 | "This is useless, get me your manager" | `transfer_to_human` | Agent apologises, says a teammate will contact them |
| 9 | "Mujhe interest nahi hai" | `mark_not_interested` | Agent thanks politely and closes |
| 10 | "Samajh nahi aaya" | confused | Agent apologises and asks what they want to know |

| 11 | "Yes speaking" / "haan bol raha hoon" | identification | Agent thanks them and asks what business they run |
| 12 | "We run a travel agency, our problem is manual follow-up" | discovery (business type and pain point stored) | Agent offers a quick call; later calls do not ask again |
| 13 | "Koi discount milega?" | discount request, no approved offer | Agent promises nothing, offers a call with the team |
| 14 | "Ok thanks, bye" | closing | Agent says goodbye, no new pitch |

### Silence and connection issue (live voice only)

Place a call and stay silent after the agent finishes speaking.

| Silence | Expected |
|---|---|
| about 8 s (`CONVERSATION_SILENCE_CHECK_SECONDS`) | One short, gentle "are you still there?" in the call's language; the agent does not repeat its last point |
| another 8 s | A second gentle check (`CONVERSATION_SILENCE_MAX_CHECKS`, default 2) |
| another 8 s | One polite goodbye; the turn log shows state `connection_issue` |
| Caller speaks at any point | The counter resets; nothing further is said |

A short pause while the caller thinks (under 8 s) must cause nothing. `CONVERSATION_SILENCE_CHECK_SECONDS=0` switches silence handling off.

### Smart Script (Organizations -> Edit -> Smart Script)

With Voice calls = Live: busy / callback / appointment / not interested / hand-off turns are decided by the engine as above; every other turn (price, objection, interested ...) is answered from the org's own script with the scenario's guidance added. Confirm the agent keeps using the script wording on price and product questions.

Also check: caller interrupts mid-reply (barge-in still works), ElevenLabs voice if used.

## Acceptance

* Decision matches the expected scenario and action on >= 90 % of the rows.
* **Zero duplicate actions** (check Appointments / Human Support / leads after each call).
* Added delay before the agent speaks: rules decisions about unnoticeable; model decisions <= ~1 s. Note how often an ambiguous turn gets **no decision** (row 5 style): the turn log shows `timeout` fallbacks. If that is frequent, measure the model round-trip from the server and raise `CONVERSATION_VOICE_LLM_TIMEOUT_SECONDS` modestly, or accept that ambiguous turns stay script-driven.
* No call dropped or garbled.

## Roll back

* One org: set Voice calls to **Log only** or **Off** (takes effect within about 30 seconds), or Conversation behaviour to Strict Script.
* Everywhere: `CONVERSATION_VOICE_LIVE=false` on the box and recreate the api container.
