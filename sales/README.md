# Outbound Call Tracker

`outbound-call-tracker.csv` — one row per call, columns follow the 7-step
outbound script (see `apps/api/core/prompts.py::OUTBOUND_CALL_PROMPT`).

**Call Outcome** values: `Booked`, `Follow-up Needed`, `Not Interested`, `No Answer`, `Voicemail`.

Open the CSV in Excel/Google Sheets to log calls.
