"""Final gate audit: reads only; output is limited to safe facts and booleans.

--provider-metadata performs only GET subscription/account metadata, never
generation, telephony, webhook changes, Redis mutation, or app startup.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["VEEROX_ENV_FILE"] = str(ROOT / ".env.pilot")
logging.disable(logging.CRITICAL)

from apps.api.config import settings  # noqa: E402
from apps.api.pilot_checks import check_pilot_env, parse_env  # noqa: E402


async def audit(provider_metadata: bool = False) -> dict:
    import httpx
    import redis.asyncio as redis
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from apps.api.channels.voice import realtime_bridge, signature
    from apps.api.channels.voice.greeting import has_ai_disclosure
    from apps.api.core.conversation.config import config_from_json

    pilot = parse_env((ROOT / ".env.pilot").read_text(encoding="utf-8"))
    main = parse_env((ROOT / ".env").read_text(encoding="utf-8"))
    checks = check_pilot_env(pilot, main)
    base = urlsplit(settings.public_base_url)
    allowed = [re.sub(r"\D", "", n) for n in settings.pilot_allowed_numbers.split(",") if n.strip()]
    result = {
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "settings": {
            k: getattr(settings, k)
            for k in (
                "environment",
                "pilot_workers_enabled",
                "pilot_allow_provider_config_changes",
                "pilot_max_outbound_calls",
                "max_concurrent_calls",
                "voice_outbound_time_limit_seconds",
                "voice_outbound_ring_timeout_seconds",
                "voice_reconnect_recovery",
                "voice_live_turn_detection",
                "voice_smart_turn_taking",
                "voice_tts_recovery",
                "voice_greeting_ai_disclosure",
                "log_transcripts",
                "log_privacy_enabled",
                "plivo_startup_registration",
                "plivo_signature_mode",
                "plivo_stream_token_ttl_seconds",
                "plivo_stream_token_max_uses",
                "plivo_nonce_ttl_seconds",
            )
        },
        "effective_signature_mode": signature.effective_mode(),
        "provider_credentials_shared_with_main": {
            "openai": bool(pilot.get("OPENAI_API_KEY"))
            and pilot.get("OPENAI_API_KEY") == main.get("OPENAI_API_KEY"),
            "elevenlabs": bool(pilot.get("ELEVENLABS_API_KEY"))
            and pilot.get("ELEVENLABS_API_KEY") == main.get("ELEVENLABS_API_KEY"),
            "plivo": bool(pilot.get("PLIVO_AUTH_ID"))
            and pilot.get("PLIVO_AUTH_ID") == main.get("PLIVO_AUTH_ID"),
        },
        "allowlisted_destination_count": len(set(allowed)),
        "public_base": {
            "https": base.scheme == "https",
            "has_host": bool(base.hostname),
            "root_path": base.path in ("", "/"),
            "no_userinfo_query_or_fragment": not bool(
                base.username or base.password or base.query or base.fragment
            ),
            "shared_with_main": settings.public_base_url.rstrip("/")
            == main.get("PUBLIC_BASE_URL", "").rstrip("/"),
        },
        "offline_preflight": [{"status": c.status, "check": c.check} for c in checks],
        "file_vs_shell_overrides": sorted(
            k for k in pilot if k in os.environ and os.environ[k] != pilot[k]
        ),
        "environment_integrations_disabled": {
            "meta": not any((settings.meta_access_token, settings.meta_app_secret)),
            "brevo": not bool(settings.brevo_api_key),
            "twilio": not any((settings.twilio_account_sid, settings.twilio_auth_token)),
        },
        "providers": {
            "openai": {
                "status": "BLOCKED",
                "reason": (
                    "No console/admin billing access; "
                    "ordinary model key does not attest project spend policy."
                ),
            }
        },
    }
    guard_fail = settings.environment != "pilot" or any(c.status == "FAIL" for c in checks)
    if guard_fail:
        result["database"] = {"status": "BLOCKED", "reason": "offline_preflight_failed"}
        return result
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=1,
        max_overflow=0,
        connect_args={"statement_cache_size": 0, "timeout": 10, "command_timeout": 15},
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            if (await conn.execute(text("SHOW transaction_read_only"))).scalar_one() != "on":
                raise RuntimeError("read_only_guard")
            db_name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
            if db_name.lower() != pilot.get("PILOT_DATABASE_NAME", "").lower():
                raise RuntimeError("database_guard")
            org = (
                (
                    await conn.execute(
                        text(
                            "SELECT name, voice_greeting, voice_brand, "
                            "voice_compliance, voice_profile, "
                            "conversation_mode, conversation_voice_mode, "
                            "(meta_access_token_encrypted IS NOT NULL OR "
                            "meta_app_secret_encrypted IS NOT NULL) AS meta_stored, "
                            "(twilio_account_sid IS NOT NULL OR "
                            "twilio_auth_token_encrypted IS NOT NULL) AS twilio_stored "
                            "FROM orgs WHERE id::text=:org"
                        ),
                        {"org": settings.default_org_id},
                    )
                )
                .mappings()
                .one()
            )
            preview = json.loads(
                (ROOT / "docs/pilot-org-config.preview.json").read_text(encoding="utf-8")
            )
            want_script = preview["script"]["body"]["content"]
            scripts = (
                (
                    await conn.execute(
                        text(
                            "SELECT content, channel, is_default FROM scripts "
                            "WHERE org_id::text=:org"
                        ),
                        {"org": settings.default_org_id},
                    )
                )
                .mappings()
                .all()
            )
            knowledge = (
                (
                    await conn.execute(
                        text(
                            "SELECT kind,title,body,active FROM knowledge_items "
                            "WHERE org_id::text=:org"
                        ),
                        {"org": settings.default_org_id},
                    )
                )
                .mappings()
                .all()
            )
            pub = (
                (
                    await conn.execute(
                        text(
                            "SELECT version,config FROM conversation_config_versions "
                            "WHERE org_id::text=:org "
                            "AND status='published' ORDER BY version DESC LIMIT 1"
                        ),
                        {"org": settings.default_org_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            model = config_from_json(pub["config"] if pub else None)

            # Run the actual pipeline loaders inside the same read-only DB transaction.
            class ReadOnlySession:
                async def __aenter__(self):
                    self.session = AsyncSession(bind=conn, expire_on_commit=False)
                    return self.session

                async def __aexit__(self, *args):
                    await self.session.close()

            original = realtime_bridge.AsyncSessionLocal
            try:
                realtime_bridge.AsyncSessionLocal = ReadOnlySession
                from uuid import UUID

                pipeline_script = await realtime_bridge._system_instructions(
                    None, UUID(settings.default_org_id)
                )
                pipeline_greeting = await realtime_bridge._load_org_greeting(
                    UUID(settings.default_org_id)
                )
            finally:
                realtime_bridge.AsyncSessionLocal = original
            expected_k = {
                (x["kind"], x["title"], x["body"], x["active"])
                for x in preview["knowledge"]["items"]
            }
            result["database"] = {
                "status": "PASS",
                "read_only": True,
                "declared_pilot_database_matches": True,
            }
            result["organization"] = {
                "name_is_veerox_pilot": org["name"] == "Veerox Pilot",
                "selected_by_pilot_default_org_id": True,
                "conversation_mode": org["conversation_mode"],
                "voice_decision_mode": org["conversation_voice_mode"],
                "enhanced_dialogue": model.enhanced_dialogue,
                "published_config_version": pub["version"] if pub else None,
                "recording_off": (org["voice_compliance"] or {}).get("recording_mode") == "off",
                "greeting_matches_reviewed_preview": org["voice_greeting"]
                == preview["greeting"]["body"]["greeting"],
                "greeting_ai_disclosure": has_ai_disclosure(org["voice_greeting"] or ""),
                "default_voice_script_matches_reviewed_preview": sum(
                    x["content"] == want_script and x["channel"] == "voice" and x["is_default"]
                    for x in scripts
                )
                == 1,
                "knowledge_count": len(knowledge),
                "knowledge_matches_reviewed_preview": {
                    (x["kind"], x["title"], x["body"], x["active"]) for x in knowledge
                }
                == expected_k,
                "pipeline_uses_reviewed_script": want_script in pipeline_script,
                "pipeline_uses_reviewed_greeting": pipeline_greeting
                == preview["greeting"]["body"]["greeting"],
                "meta_org_credentials_absent": not org["meta_stored"],
                "twilio_org_credentials_absent": not org["twilio_stored"],
                "voice_profile_absent": not bool(org["voice_profile"]),
                "configured_caller_id_shared_with_main": bool(pilot.get("PLIVO_PHONE_NUMBER"))
                and pilot.get("PLIVO_PHONE_NUMBER") == main.get("PLIVO_PHONE_NUMBER"),
            }
            await conn.rollback()
    except Exception as exc:
        result["database"] = {"status": "BLOCKED", "error_type": type(exc).__name__}
    finally:
        await engine.dispose()
    redis_client = redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    try:
        result["redis"] = {
            "status": "PASS" if await redis_client.ping() else "FAIL",
            "command": "PING only",
        }
    except Exception as exc:
        result["redis"] = {"status": "BLOCKED", "error_type": type(exc).__name__}
    finally:
        await redis_client.aclose()
    if provider_metadata:
        # An exact allowlist: never follow redirects or send a generation/call request.
        async with httpx.AsyncClient(timeout=12, trust_env=False, follow_redirects=False) as http:
            try:
                response = await http.get(
                    "https://api.elevenlabs.io/v1/user/subscription",
                    headers={"xi-api-key": settings.elevenlabs_api_key or ""},
                )
                if response.status_code == 200:
                    body = response.json()
                    result["providers"]["elevenlabs"] = {
                        "status": "PASS",
                        **{
                            k: body[k]
                            for k in (
                                "tier",
                                "character_count",
                                "character_limit",
                                "max_credit_limit_extension",
                                "can_extend_character_limit",
                                "allowed_to_extend_character_limit",
                                "billing_period",
                            )
                            if k in body
                            and isinstance(body[k], (str, int, float, bool, type(None)))
                        },
                    }
                else:
                    result["providers"]["elevenlabs"] = {
                        "status": "BLOCKED",
                        "http_status": response.status_code,
                    }
            except Exception as exc:
                result["providers"]["elevenlabs"] = {
                    "status": "BLOCKED",
                    "error_type": type(exc).__name__,
                }
            try:
                response = await http.get(
                    f"https://api.plivo.com/v1/Account/{settings.plivo_auth_id}/",
                    auth=(settings.plivo_auth_id or "", settings.plivo_auth_token or ""),
                )
                if response.status_code == 200:
                    body = response.json()
                    result["providers"]["plivo"] = {
                        "status": "PASS",
                        **{
                            k: body[k]
                            for k in (
                                "cash_credits",
                                "auto_recharge",
                                "billing_mode",
                                "account_type",
                            )
                            if k in body
                            and isinstance(body[k], (str, int, float, bool, type(None)))
                        },
                    }
                else:
                    result["providers"]["plivo"] = {
                        "status": "BLOCKED",
                        "http_status": response.status_code,
                    }
            except Exception as exc:
                result["providers"]["plivo"] = {
                    "status": "BLOCKED",
                    "error_type": type(exc).__name__,
                }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-metadata", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(audit(args.provider_metadata)), indent=2))
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
