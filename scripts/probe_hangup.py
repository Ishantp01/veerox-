"""LIVE TELEPHONY PROBE (not run): does the provider API really end a call, and delete a recording?

    PLIVO_AUTH_ID=... PLIVO_AUTH_TOKEN=... python scripts/probe_hangup.py plivo hangup <call_uuid> --i-approve-live-telephony
    PLIVO_AUTH_ID=... PLIVO_AUTH_TOKEN=... python scripts/probe_hangup.py plivo delete-recording <recording_id> --i-approve-live-telephony
    TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... python scripts/probe_hangup.py twilio hangup <CallSid> --i-approve-live-telephony

The call or recording ids must belong to a TEST call you placed yourself to a phone you hold. The script
only calls the same client functions the application uses and prints their result (ok / already_ended /
already_gone / failed), so a pass here means the application's own code path works against the real provider.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from apps.api.channels.voice import plivo_client, twilio_client
from apps.api.core.org_credentials import PlivoCredentials, TwilioCredentials


async def run(args: argparse.Namespace) -> str:
    if args.provider == "plivo":
        creds = PlivoCredentials(auth_id=os.environ["PLIVO_AUTH_ID"], auth_token=os.environ["PLIVO_AUTH_TOKEN"])
        client = plivo_client
    else:
        creds = TwilioCredentials(account_sid=os.environ["TWILIO_ACCOUNT_SID"], auth_token=os.environ["TWILIO_AUTH_TOKEN"])
        client = twilio_client
    if args.action == "hangup":
        return await client.hangup_call(creds, args.target)
    return await client.delete_recording(creds, args.target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("provider", choices=["plivo", "twilio"])
    parser.add_argument("action", choices=["hangup", "delete-recording"])
    parser.add_argument("target")
    parser.add_argument("--i-approve-live-telephony", action="store_true", required=True)
    print(asyncio.run(run(parser.parse_args())))
    sys.exit(0)
