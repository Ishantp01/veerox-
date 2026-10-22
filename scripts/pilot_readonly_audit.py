"""Read-only pilot audit. Prints allowlisted configuration facts, never credentials.

No application startup, model calls, Redis writes, or configuration changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["VEEROX_ENV_FILE"] = str(ROOT / ".env.pilot")

from apps.api.config import settings  # noqa: E402
from apps.api.pilot_checks import check_pilot_env, parse_env  # noqa: E402


async def audit() -> dict:
    import httpx
    import redis.asyncio as redis
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from apps.api.channels.voice.greeting import default_greeting_example, has_ai_disclosure
    from apps.api.channels.voice.voice_profile import resolve_profile
    from apps.api.core.conversation.config import config_from_json
    from apps.api.core.privacy import redact_text
    from apps.api.core.tools import TOOL_DEFINITIONS

    # Connection exceptions and HTTP request logs may contain URLs or headers.
    logging.disable(logging.CRITICAL)
    pilot = parse_env((ROOT / ".env.pilot").read_text(encoding="utf-8"))
    main = parse_env((ROOT / ".env").read_text(encoding="utf-8"))
    preflight = check_pilot_env(pilot, main)
    result = {
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "credential_rotation": "waived_by_user; not performed or certified",
        "preflight": [{"status": r.status, "check": r.check} for r in preflight],
        "file_settings": {
            key: getattr(settings, key)
            for key in (
                "environment",
                "voice_reconnect_recovery",
                "voice_smart_turn_taking",
                "voice_tts_recovery",
                "voice_safe_cancel",
                "voice_nonblocking_tts_finish",
                "voice_elevenlabs_prewarm",
                "conversation_voice_live",
                "voice_tts_provider",
                "openai_realtime_model",
                "openai_chat_model",
                "elevenlabs_model_id",
                "voice_live_transcribe_model",
                "voice_live_turn_detection",
                "voice_tts_first_audio_timeout_seconds",
                "voice_reconnect_budget_seconds",
                "log_privacy_enabled",
                "log_transcripts",
                "pilot_workers_enabled",
                "pilot_allow_provider_config_changes",
                "plivo_startup_registration",
            )
        },
        "shell_override_names": sorted(
            k for k in pilot if k in os.environ and os.environ[k] != pilot[k]
        ),
    }
    # Do not reach a known production host, or use a non-pilot runtime.
    if settings.environment != "pilot" or any(
        r.status == "FAIL" and r.check == "no known production host" for r in preflight
    ):
        result["database"] = {"status": "FAIL", "reason": "pilot_target_guard"}
        return result
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=1,
        max_overflow=0,
        connect_args={"statement_cache_size": 0, "timeout": 10, "command_timeout": 10},
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            readonly = (await conn.execute(text("SHOW transaction_read_only"))).scalar_one()
            database = (await conn.execute(text("SELECT current_database()"))).scalar_one()
            if readonly != "on" or database.lower() != pilot.get("PILOT_DATABASE_NAME", "").lower():
                result["database"] = {"status": "FAIL", "reason": "readonly_or_database_guard"}
                return result
            org_id = str(UUID(settings.default_org_id))
            org = (
                (
                    await conn.execute(
                        text(
                            "SELECT name, conversation_mode, conversation_voice_mode, "
                            "voice_greeting, "
                            "voice_brand, voice_profile, voice_compliance, enabled_features, "
                            "(script IS NOT NULL AND length(script)>0) AS legacy_script_present "
                            "FROM orgs WHERE id::text=:org"
                        ),
                        {"org": org_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if org is None:
                result["database"] = {"status": "FAIL", "reason": "intended_org_missing"}
                return result
            published = (
                (
                    await conn.execute(
                        text(
                            "SELECT version, config FROM conversation_config_versions "
                            "WHERE org_id::text=:org AND status='published' "
                            "ORDER BY version DESC LIMIT 1"
                        ),
                        {"org": org_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            payload = published["config"] if published else None
            config = config_from_json(payload)
            knowledge = (
                (
                    await conn.execute(
                        text(
                            "SELECT count(*) AS total, count(*) FILTER (WHERE active) AS active, "
                            "count(*) FILTER (WHERE active AND length(trim(body))>0) "
                            "AS active_nonempty "
                            "FROM knowledge_items WHERE org_id::text=:org"
                        ),
                        {"org": org_id},
                    )
                )
                .mappings()
                .one()
            )
            scripts = (
                (
                    await conn.execute(
                        text(
                            "SELECT channel, count(*) AS total, "
                            "count(*) FILTER (WHERE is_default) AS defaults, "
                            "count(*) FILTER (WHERE length(trim(content))>0) AS nonempty "
                            "FROM scripts WHERE org_id::text=:org GROUP BY channel ORDER BY channel"
                        ),
                        {"org": org_id},
                    )
                )
                .mappings()
                .all()
            )
            profile = resolve_profile(org["voice_profile"])
            brand = org["voice_brand"] or settings.voice_brand_name
            greeting = org["voice_greeting"]
            result["database"] = {
                "status": "PASS",
                "transaction_read_only": True,
                "declared_database_matches": True,
                "intended_org_found": True,
                "selected_by": "pilot DEFAULT_ORG_ID; never first-row fallback",
            }
            result["organization"] = {
                "conversation_mode": org["conversation_mode"],
                "voice_decision_mode": org["conversation_voice_mode"],
                "live_decision_effective": bool(
                    settings.conversation_voice_live
                    and org["conversation_mode"] in ("human_conversation", "smart_script")
                    and org["conversation_voice_mode"] == "live"
                ),
                "greeting_source": "custom"
                if greeting
                else "platform Hindi/AI-disclosure instruction",
                "greeting_characters": len(greeting or ""),
                "effective_brand": redact_text(brand),
                "greeting_preview": redact_text(
                    greeting
                    or default_greeting_example(brand, settings.voice_greeting_ai_disclosure)
                ),
                "greeting_preview_is_template": not bool(greeting),
                "greeting_has_ai_disclosure": has_ai_disclosure(greeting)
                if greeting
                else settings.voice_greeting_ai_disclosure,
                "fallback_greeting_characters": len(
                    default_greeting_example(brand, settings.voice_greeting_ai_disclosure)
                ),
                "custom_brand": bool(org["voice_brand"]),
                "voice_profile_present": bool(org["voice_profile"]),
                "natural_hinglish": profile.natural_hinglish if profile else False,
                "normalize_speech": profile.normalize if profile else False,
                "recording_mode": (org["voice_compliance"] or {}).get(
                    "recording_mode", "unspecified"
                ),
                "business_knowledge": dict(knowledge),
                "scripts": [dict(s) for s in scripts],
                "legacy_script_present": org["legacy_script_present"],
                "published_config_version": published["version"] if published else None,
                "effective_config_source": "published" if published else "builtin_defaults",
                "enhanced_dialogue": config.enhanced_dialogue,
                "enabled_scenarios": config.enabled_ids(),
                "scenario_action_union": sorted(
                    {a for s in config.scenarios.values() if s.enabled for a in s.actions}
                ),
                "exposed_realtime_tools": sorted(t["function"]["name"] for t in TOOL_DEFINITIONS),
                "enabled_features": org["enabled_features"],
                "action_note": (
                    "Tool exposure is not per-call approval; "
                    "decision validation and tool-specific checks also apply."
                ),
            }
            exists = (
                await conn.execute(text("SELECT to_regclass('public.cost_rates') IS NOT NULL"))
            ).scalar_one()
            if exists:
                rates = (
                    (
                        await conn.execute(
                            text(
                                "SELECT service, usage_type, provider, unit_cost::text, currency "
                                "FROM cost_rates WHERE effective_from<=now() "
                                "AND (effective_to IS NULL OR effective_to>now())"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                result["current_cost_rates"] = [dict(r) for r in rates]
            await conn.rollback()
    except Exception as exc:
        result["database"] = {"status": "FAIL", "error_type": type(exc).__name__}
    finally:
        await engine.dispose()
    client = redis.from_url(settings.redis_url, socket_connect_timeout=5, socket_timeout=5)
    try:
        result["redis_current_auth"] = {
            "status": "PASS" if await client.ping() else "FAIL",
            "operation": "PING only",
        }
    except Exception as exc:
        result["redis_current_auth"] = {"status": "FAIL", "error_type": type(exc).__name__}
    finally:
        await client.aclose()
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
            response = await http.get(
                "http://127.0.0.1:8002/diag/voice-config",
                headers={"X-Admin-Token": settings.admin_token},
            )
            if response.status_code == 200:
                data = response.json()
                result["live_process_flags"] = {
                    k: bool(data.get("flags", {}).get(k))
                    for k in (
                        "voice_reconnect_recovery",
                        "voice_smart_turn_taking",
                        "voice_tts_recovery",
                        "log_privacy_enabled",
                        "log_transcripts",
                    )
                }
            else:
                result["live_process_flags"] = {
                    "status": "FAIL",
                    "http_status": response.status_code,
                }
    except Exception as exc:
        result["live_process_flags"] = {"status": "FAIL", "error_type": type(exc).__name__}
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(audit()), indent=2))
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
