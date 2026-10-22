r"""Point the pilot's Plivo number at the pilot tunnel for INBOUND calls, using a DEDICATED Plivo application.

The built-in registration (``plivo_client.register_inbound_answer_url``) edits an account-wide application named
``veerox-voice-app`` and attaches the number to it. If any other number shares that application, they would all be
rerouted. This script never touches it: it creates/updates its own ``veerox-pilot-app`` and attaches ONLY the one pilot
number, after saving what the number used before so it can be put back.

    $env:VEEROX_ENV_FILE = (Resolve-Path .env.pilot).Path
    python scripts\pilot_plivo_inbound.py .env.pilot                  # read-only: what is the number attached to today?
    python scripts\pilot_plivo_inbound.py .env.pilot --apply --i-approve-changing-this-one-number
    python scripts\pilot_plivo_inbound.py .env.pilot --rollback --i-approve-changing-this-one-number

Refuses to ``--apply`` if the number's current Answer URL points at a production host (the line is in real use), if
the pilot guard rails are not on, or if the number is not the pilot org's line. Only GETs happen unless a change flag
is given. It never prints the account id, tokens, phone numbers or full URLs (host names only).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from apps.api.pilot_checks import BUILTIN_FORBIDDEN_FRAGMENTS, parse_env  # noqa: E402

PLIVO = "https://api.plivo.com/v1"
APP_NAME = "veerox-pilot-app"
DEFAULT_STATE = ".pilot_plivo_state.json"


def host_of(url: str | None) -> str:
    return (urlparse(url or "").hostname or "(none)").lower()


def app_id_from(number_obj: dict) -> str | None:
    value = number_obj.get("app_id") or number_obj.get("application") or ""
    match = re.search(r"([0-9a-f]{8,})/?$", str(value))
    return match.group(1) if match else (str(value) or None)


async def discover(client: httpx.AsyncClient, base: str, number: str) -> dict:
    apps = (await client.get(f"{base}/Application/", params={"limit": 20})).json().get("objects", [])
    by_id = {a["app_id"]: a for a in apps}
    me = (await client.get(f"{base}/Number/{number}/")).json()
    mine = app_id_from(me)
    attached = 0
    offset = 0
    while offset < 200:
        page = (await client.get(f"{base}/Number/", params={"limit": 20, "offset": offset})).json().get("objects", [])
        attached += sum(1 for n in page if mine and app_id_from(n) == mine and str(n.get("number", "")).lstrip("+") != number)
        if len(page) < 20:
            break
        offset += 20
    current = by_id.get(mine or "", {})
    return {
        "applications_on_account": len(apps),
        "number_found": bool(me.get("number") or me.get("alias") is not None or me.get("app_id") is not None or me.get("application")),
        "current_app_id": mine,
        "current_app_name": current.get("app_name"),
        "current_answer_url_host": host_of(current.get("answer_url")),
        "other_numbers_on_same_app": attached,
        "pilot_app_exists": any(a.get("app_name") == APP_NAME for a in apps),
        "pilot_app_id": next((a["app_id"] for a in apps if a.get("app_name") == APP_NAME), None),
    }


async def run(args: argparse.Namespace) -> int:
    env = parse_env(Path(args.pilot_env).read_text(encoding="utf8"))
    if env.get("ENVIRONMENT", "").lower() != "pilot":
        print("REFUSED: the pilot file must have ENVIRONMENT=pilot")
        return 1
    number = re.sub(r"\D", "", env.get("PLIVO_PHONE_NUMBER", ""))
    if not number or not env.get("PLIVO_AUTH_ID") or not env.get("PLIVO_AUTH_TOKEN"):
        print("REFUSED: PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN and PLIVO_PHONE_NUMBER must be set in the pilot file")
        return 1
    base = f"{PLIVO}/Account/{env['PLIVO_AUTH_ID']}"
    answer_url = env["PUBLIC_BASE_URL"].rstrip("/") + "/voice/answer"
    state_path = Path(args.state_file)
    async with httpx.AsyncClient(auth=(env["PLIVO_AUTH_ID"], env["PLIVO_AUTH_TOKEN"]), timeout=20) as client:
        info = await discover(client, base, number)
        print("current state:", {k: v for k, v in info.items() if k != "current_app_id" and k != "pilot_app_id"})
        if not (args.apply or args.rollback):
            print("read-only run: nothing was changed.")
            return 0
        if not args.i_approve_changing_this_one_number:
            print("REFUSED: needs --i-approve-changing-this-one-number")
            return 1
        if args.rollback:
            if not state_path.exists():
                print("REFUSED: no saved state to roll back to")
                return 1
            previous = json.loads(state_path.read_text())["previous_app_id"]
            if previous:
                r = await client.post(f"{base}/Number/{number}/", json={"app_id": previous})
                print("rollback: number reattached to its previous application:", r.status_code in (200, 202))
                return 0 if r.status_code in (200, 202) else 1
            print("rollback: the number had no application before; nothing to reattach (remove the pilot app by hand if wanted)")
            return 0
        # --apply
        bad = [f for f in BUILTIN_FORBIDDEN_FRAGMENTS if f in info["current_answer_url_host"]]
        if bad:
            print("REFUSED: the number's current Answer URL points at a production host: the line is in real use")
            return 1
        if not info["number_found"]:
            print("REFUSED: the number was not found on this Plivo account")
            return 1
        state_path.write_text(json.dumps({"previous_app_id": info["current_app_id"], "previous_app_name": info["current_app_name"]}))
        payload = {"answer_url": answer_url, "answer_method": "POST"}
        if info["pilot_app_id"]:
            app_id = info["pilot_app_id"]
            r = await client.post(f"{base}/Application/{app_id}/", json=payload)
        else:
            r = await client.post(f"{base}/Application/", json={"app_name": APP_NAME, **payload})
            app_id = (r.json() or {}).get("app_id") if r.status_code in (200, 201, 202) else None
        if r.status_code not in (200, 201, 202) or not app_id:
            print("FAILED to create/update the pilot application:", r.status_code)
            return 1
        r = await client.post(f"{base}/Number/{number}/", json={"app_id": app_id})
        ok = r.status_code in (200, 202)
        after = await discover(client, base, number)
        print("attached ONLY the pilot number to the dedicated application:", ok,
              "| now:", {"app": after["current_app_name"], "answer_url_host": after["current_answer_url_host"]})
        print("previous attachment saved for rollback in", state_path.name)
        return 0 if ok and after["current_app_name"] == APP_NAME else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pilot_env")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--i-approve-changing-this-one-number", dest="i_approve_changing_this_one_number", action="store_true")
    parser.add_argument("--state-file", default=DEFAULT_STATE)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
