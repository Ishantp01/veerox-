"""Adversarial logging verification using synthetic data only.

Prints case names and PASS/FAIL, never the captured log lines or injected data.
Exit 1 means the current logging policy still exposes a tested class of data.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["VEEROX_ENV_FILE"] = str(ROOT / ".env.pilot")

import structlog  # noqa: E402

from apps.api.config import settings  # noqa: E402
from apps.api.logging import setup_logging  # noqa: E402


def verify() -> dict:
    stream, original = io.StringIO(), sys.stdout
    results = []
    # Suppress real settings values; only these fabricated markers enter logs.
    phone = "+919876543210"
    transcript = "SYNTHETIC-PRIVATE-SENTENCE"
    credential = "SYNTHETIC-PRIVATE-CREDENTIAL"
    payload = "SYNTHETIC-PRIVATE-PROVIDER-PAYLOAD"
    try:
        sys.stdout = stream
        setup_logging()
        log = structlog.get_logger("offline.privacy.verification")

        def check(name, emit, forbidden):
            stream.seek(0)
            stream.truncate()
            emit()
            output = stream.getvalue()
            results.append(
                {
                    "check": name,
                    "status": "PASS"
                    if output and not any(item in output for item in forbidden)
                    else "FAIL",
                }
            )

        check(
            "structured_caller_number",
            lambda: log.info("voice_stream_connected", caller=phone),
            [phone, phone[1:]],
        )
        check(
            "uvicorn_websocket_url",
            lambda: logging.getLogger("uvicorn.error").warning(
                "WebSocket /voice?caller=%2B919876543210"
            ),
            [phone, phone[1:]],
        )
        check(
            "caller_transcript",
            lambda: log.info("voice_user_transcript", text=transcript),
            [transcript],
        )
        check(
            "assistant_transcript",
            lambda: log.info("voice_assistant_transcript", text=transcript),
            [transcript],
        )
        check(
            "database_url_credentials",
            lambda: log.warning(
                "database_failed", error=f"postgresql://offline:{credential}@localhost/pilot"
            ),
            [credential],
        )
        check(
            "redis_url_credentials",
            lambda: log.warning(
                "redis_failed", error=f"redis://default:{credential}@localhost:6379/0"
            ),
            [credential],
        )
        check(
            "authorization_headers",
            lambda: log.warning(
                "provider_failed", headers={"Authorization": f"Bearer {credential}"}
            ),
            [credential],
        )
        check(
            "nested_webhook_parameters",
            lambda: log.warning(
                "plivo_recording_callback_missing_fields",
                params={"From": phone, "transcript": transcript},
            ),
            [phone, phone[1:], transcript],
        )
        check(
            "nested_provider_error",
            lambda: log.warning("elevenlabs_tts_error", error={"detail": {"payload": payload}}),
            [payload],
        )
        check(
            "provider_response_body",
            lambda: log.warning("twilio_initiate_call_failed", body=payload, status=400),
            [payload],
        )

        def exception():
            try:
                raise RuntimeError(f"caller {phone}")
            except RuntimeError:
                logging.getLogger("offline.exception").exception("failed")

        check("exception_phone_number", exception, [phone, phone[1:]])
    finally:
        sys.stdout = original
    return {
        "effective_log_transcripts": settings.log_transcripts,
        "effective_log_privacy_enabled": settings.log_privacy_enabled,
        "synthetic_data_only": True,
        "checks": results,
        "status": "PASS" if all(r["status"] == "PASS" for r in results) else "FAIL",
    }


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
