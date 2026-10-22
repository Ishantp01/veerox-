# Pilot Org business configuration: PREVIEW (nothing written)

Source of truth: [`pilot-org-config.preview.json`](pilot-org-config.preview.json). `apps/api/tests/test_pilot_org_config_preview.py`
checks it against the real admin schemas and validators. **Every business fact in it is fictional** (marked
SYNTHETIC) and must be replaced with approved content before any real customer call. No database has been written.

## What shapes a call today, and which existing mechanism carries each part

| Part | Existing mechanism | Read by | Effect on the first call |
|---|---|---|---|
| Opening line with AI disclosure | `PUT /admin/settings/voice-greeting` (validated: ≤300 characters, must say it is an AI) | the voice bridge, verbatim | yes |
| Identity, objective, approved facts, objection handling, callback/appointment rules, escalation, restrictions | `POST /admin/scripts` (first script becomes the org's default voice script) | the voice bridge builds the Realtime prompt from it | **yes: this is where the rules live today** |
| FAQ / product / pricing / policy facts | `POST /conversation-engine/knowledge` | the decision layer, once the org is moved from `shadow` to `live` | no (org stays in `shadow`) |
| Brand wording in the platform rules | `VOICE_BRAND_NAME` in `.env.pilot` (the org has no brand and there is no admin route to set one for an existing org) | prompt example text | yes; no database write |
| Scenario config, `enhanced_dialogue`, voice profile (`natural_hinglish`, `normalize_speech`) | existing draft/publish and voice-profile endpoints | later steps | not applied |

No second configuration system was built. The legacy script path is what the pilot org uses, so the rules are in the
script; the same facts are also prepared as knowledge items so the later decision-layer step does not need new content.

## What the preview says

- **Greeting:** "Namaste, main Veerox ka AI assistant Veer bol raha hoon. Kya main aapse do minute baat kar sakta hoon?"
- **Identity and objective:** Veer, an AI assistant calling for Veerox to find out whether the contact wants a short demo; at
  most one callback or demo offer, only to an interested caller.
- **Facts (fictional, and the only ones the agent may use):** Hindi/Hinglish/English AI assistant for calls and WhatsApp; a demo of
  about 15 minutes; slots Monday to Saturday, 10:00 to 18:00 IST.
- **Restrictions:** no price, discount, saving, return, percentage, customer count or guarantee; nothing invented about the company; no
  promised call time without a saved callback; no legal, financial or technical advice. A pricing question gets "the team will
  share exact plan details".
- **Objections:** expensive; not interested (mark lost only if the whole offer is declined, not a meeting); needs family or partner;
  call later; no meeting wanted (never offered again that call); send on WhatsApp (claim "sent" only after the tool succeeds).
- **Appointments and callbacks:** the caller must clearly agree to a specific day and time inside the window; the agent repeats it
  back and waits for a yes; corrections are confirmed again; a callback time is asked for, never chosen. Tool failures are reported
  honestly.
- **Human escalation:** use `transfer_to_human`, say the team will follow up, never that someone is available now.

## Applying it (needs your approval; writes only to the pilot database)

All through existing endpoints, with the pilot admin token, against the local pilot API:

```powershell
$env:VEEROX_ENV_FILE = (Resolve-Path .env.pilot).Path
$cfg = Get-Content docs\pilot-org-config.preview.json -Raw | ConvertFrom-Json
$h = @{ 'X-Admin-Token' = '<pilot ADMIN_TOKEN>'; 'Content-Type' = 'application/json' }
$b = 'http://127.0.0.1:8002'
Invoke-RestMethod -Method Put  -Headers $h "$b/admin/settings/voice-greeting" -Body ($cfg.greeting.body | ConvertTo-Json -Depth 5)
Invoke-RestMethod -Method Post -Headers $h "$b/admin/scripts"                  -Body ($cfg.script.body   | ConvertTo-Json -Depth 5)
foreach ($item in $cfg.knowledge.items) { Invoke-RestMethod -Method Post -Headers $h "$b/conversation-engine/knowledge" -Body ($item | ConvertTo-Json -Depth 5) }
```

Add `VOICE_BRAND_NAME=Veerox` to `.env.pilot` and restart the API. Read everything back (`GET` on the same paths) before the call.

## Rolling it back

```powershell
Invoke-RestMethod -Method Put    -Headers $h "$b/admin/settings/voice-greeting" -Body '{"greeting": null}'   # back to the default greeting
$scripts = Invoke-RestMethod -Headers $h "$b/admin/scripts"; # find the pilot script's id, then:
Invoke-RestMethod -Method Delete -Headers $h "$b/admin/scripts/<script_id>"
$items = Invoke-RestMethod -Headers $h "$b/conversation-engine/knowledge"; # then DELETE /conversation-engine/knowledge/<id> for each
```

Remove `VOICE_BRAND_NAME` from `.env.pilot`. Because the pilot database is disposable, dropping the Neon project also removes everything.

## What still needs a human

Approval of the wording and of every fact. A real business owner must supply the approved facts, the demo window and the
escalation owner before this is used with a real person.
