# Pilot spending safeguards: how to check them (nothing here has been checked or changed)

**There is no hard monetary cap in this software and none should be assumed.** The code limits the *quantity* of a pilot run: one
outbound call per process, one concurrent call, 120 s requested from Plivo, a 25 s ring timeout. It cannot see or limit money. The
OpenAI, ElevenLabs and Plivo keys in `.env.pilot` are the **same** as the main `.env`, so a limit you set on them also limits production,
and production spending counts against them.

I could not look at any provider console, and I did not call any provider API to read balances or limits. The steps below come from
each provider's public documentation; menu names change, so trust the console over this page. Write the result of each check in the
last column and keep it with the call record.

## What one call can use (measured on the two earlier calls, about 280 s of voice in total)

141,173 Realtime input tokens, 2,818 output tokens, 3,840 ElevenLabs characters and 255 s of transcription. A 120 s call should use
roughly half of that, plus the live turn detection's extras (one extra transcription connection for the whole call, one warm-up
request and up to three judging requests; `voice_live_turn_summary` reports the counts afterwards). Prices are not in this repository
and are not stated here: multiply those quantities by the current price on each provider's pricing page.

## OpenAI

| Check | How | Result |
|---|---|---|
| Which project the pilot key belongs to | platform.openai.com -> the project that owns the key in `.env.pilot` (the key is shown only as a prefix and last characters) | |
| A spend limit exists for that project | Project settings -> **Limits** -> monthly spend limit. OpenAI documents a *soft* limit (an alert) and a *hard* limit (API calls fail with HTTP 429 once reached). Turn the hard limit on if you want requests refused | |
| An organization-level limit | Organization settings -> **Limits**. It applies across every project, including production | |
| The approved usage tier limit | The same page shows the monthly limit OpenAI assigns your tier; it is separate from your own limit | |
| How much was used today | **Usage** page, filtered to the project and today's date, before and after the call | |

Do not rely on the limit alone: OpenAI's own documentation says enforcement is not instantaneous, and public forum reports describe
per-project limits that did not stop spending. Treat it as a second line behind the call's 120 s limit. Source:
[OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits).

## ElevenLabs

| Check | How | Result |
|---|---|---|
| The plan and remaining credits | elevenlabs.io -> profile -> **Subscription** (credits used, credits left, reset date) | |
| What happens at the limit | On the same page: whether usage-based billing (overage) is on. If it is on, usage continues past the plan's credits and is billed; if off, requests fail with `quota_exceeded`. I could not confirm a per-key or per-workspace spending cap in the documentation | |
| Credits used by the call | Subscription page before and after the call; 3,840 characters were used by the earlier ~280 s | |

Unverified: whether your plan offers any hard cap. If the plan has usage-based billing enabled, assume there is none.

## Plivo

| Check | How | Result |
|---|---|---|
| The account balance | Console -> billing/overview (prepaid balance). A zero balance blocks calls, so a prepaid balance is a real ceiling; a postpaid account is not | |
| Auto-recharge | Console -> **Billing -> Auto Recharge**. If it is on, the balance is refilled from the card at the threshold, so it is *not* a ceiling. Turn it off for the pilot only if you are sure production does not need it | |
| Low-balance alerts | Console -> **Payment Settings**: Plivo emails at fixed thresholds and you can add up to three of your own | |
| Which countries can be dialled | Console -> **Voice -> Geo Permissions**. Changes apply at once to API-placed calls. A destination that is not permitted is refused with HTTP 403 "barred". The pilot allowlist already limits destinations to your own number; this is a second line | |
| Caller ID | The call shows the production number; its minutes are billed to the production account | |

Source: [Plivo geo permissions](https://www.plivo.com/docs/voice/concepts/geo-permissions).

## Before the call, in order

1. Note the balance and usage figures above (OpenAI usage, ElevenLabs credits, Plivo balance).
2. Confirm which of the three have a limit that would actually stop spending (OpenAI hard limit, Plivo prepaid balance with auto-recharge off). Write down the ones that have none.
3. Place the call, then re-read the three figures and compare with the estimate. The difference is the real cost of this call.

Nothing in the pilot changes any of these settings, and nothing should change them automatically.
