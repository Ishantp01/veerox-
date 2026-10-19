"""Phase 6: validation tooling (scenario catalog, rating aggregation, migration guard) and the flag-combination
matrix. Offline; synthetic data only. The ratings below are made-up numbers used to test the arithmetic and the
"no conclusion without data" rule - they say nothing about any voice.
"""

from __future__ import annotations

import base64
import itertools
import json
import sys
from pathlib import Path

import pytest

from apps.api.channels.voice import call_verify, config_check, quality
from apps.api.config import settings
from apps.api.tests.test_voice_call_control import Call, provider  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[3]
CATALOG = json.loads((ROOT / "scripts" / "voice_validation_scenarios.json").read_text(encoding="utf8"))["scenarios"]


# --- scenario catalog -------------------------------------------------------------------------------------------------------------


def test_every_scenario_is_complete_and_uses_only_known_checks() -> None:
    ids = [s["id"] for s in CATALOG]
    assert len(ids) == len(set(ids)) and len(ids) >= 17
    for s in CATALOG:
        assert s["tester_script"] and s["expected"] and s["direction"] in ("inbound", "outbound")
        for check in s["auto_checks"] + s["manual_checks"]:
            assert check in call_verify.CHECKS, (s["id"], check)
        for flag in s["flags_required"]:
            assert flag.lower() in {f.lower() for f in config_check.BEHAVIOUR_FLAGS}, (s["id"], flag)


def test_the_catalog_covers_the_required_topics() -> None:
    text = json.dumps(CATALOG).lower()
    for topic in ("inbound", "outbound", "hindi", "hinglish", "interrupt", "cancel", "prewarm", "disclosure", "cache",
                  "normalis", "objection", "correction", "appointment", "ends the call", "recovery", "reconnect",
                  "consent", "retention", "metering"):
        assert topic in text, topic


# --- rating aggregation -----------------------------------------------------------------------------------------------------------


def _ratings(sheet: list[dict], raters: int, per_variant_means: dict[str, int]) -> list[dict]:
    out = []
    for r in range(raters):
        for row in sheet:
            score = per_variant_means[row["variant"]]
            out.append({"rater": f"R{r}", "label": row["label"], "scores": {"naturalness": score, "clarity": score, "pacing": score}})
    return out


def test_the_sheet_hides_variants_from_listeners_and_is_reproducible() -> None:
    prompts = ["namaste", "price kitna hai", "kal 3 baje"]
    sheet = quality.make_sheet(prompts, seed=1)
    assert len(sheet) == len(prompts) * len(quality.VARIANTS) and sheet == quality.make_sheet(prompts, seed=1)
    assert all("variant" not in row for row in quality.rater_view(sheet))


def test_no_winner_is_named_without_enough_ratings() -> None:
    sheet = quality.make_sheet(["a", "b"], seed=2)
    thin = quality.aggregate(sheet, _ratings(sheet, 1, {"elevenlabs_turbo_v2_5": 5, "elevenlabs_flash_v2_5": 1, "openai_native": 3}))
    assert thin["conclusion"]["leader"] is None and "not enough ratings" in thin["conclusion"]["reason"]
    assert quality.aggregate(sheet, [])["conclusion"]["leader"] is None


def test_a_clear_difference_with_enough_data_names_a_leader_and_a_tie_does_not() -> None:
    sheet = quality.make_sheet([f"p{i}" for i in range(4)], seed=3)
    clear = quality.aggregate(sheet, _ratings(sheet, 4, {"elevenlabs_turbo_v2_5": 5, "elevenlabs_flash_v2_5": 3, "openai_native": 2}))
    assert clear["conclusion"]["leader"] == "elevenlabs_turbo_v2_5"
    tie = quality.aggregate(sheet, _ratings(sheet, 4, {"elevenlabs_turbo_v2_5": 4, "elevenlabs_flash_v2_5": 4, "openai_native": 4}))
    assert tie["conclusion"]["leader"] is None and "no difference" in tie["conclusion"]["reason"]


def test_invalid_ratings_are_reported_and_ignored() -> None:
    sheet = quality.make_sheet(["a"], seed=4)
    label = sheet[0]["label"]
    bad = [
        {"rater": "A", "label": label, "scores": {"naturalness": 9}},
        {"rater": "A", "label": "clip-999", "scores": {"naturalness": 3}},
        {"label": label, "scores": {"naturalness": 3}},
        {"rater": "A", "label": label, "scores": {"loudness": 3}},
    ]
    result = quality.aggregate(sheet, bad)
    assert len(result["ignored"]) == 4 and result["variants"] == {}


# --- migration guard --------------------------------------------------------------------------------------------------------------


def test_the_migration_validator_refuses_production_hosts() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import validate_migrations
    finally:
        sys.path.pop(0)
    for url in (
        "postgresql+asyncpg://u:p@veerox-db.c3eoio6kg92s.us-east-2.rds.amazonaws.com/db",
        "postgresql+asyncpg://u:p@3.139.241.196/db",
        "postgresql+asyncpg://u:p@ep-x.neon.tech/db",
    ):
        with pytest.raises(SystemExit):
            validate_migrations.refuse_if_protected(url)
    validate_migrations.refuse_if_protected("postgresql+asyncpg://u:p@localhost:5432/scratch")


# --- flag-combination matrix ------------------------------------------------------------------------------------------------------

MATRIX_FLAGS = (
    "voice_safe_cancel", "voice_trim_interrupted_context", "voice_smart_turn_taking",
    "voice_end_call_enabled", "voice_tts_recovery", "voice_reconnect_recovery",
)
AUDIO = base64.b64encode(b"\xff" * 1600).decode()


@pytest.mark.parametrize("combo", list(itertools.product([False, True], repeat=len(MATRIX_FLAGS))))
async def test_a_scripted_call_behaves_the_same_under_every_flag_combination(
    combo: tuple[bool, ...], provider, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    for name, value in zip(MATRIX_FLAGS, combo):
        monkeypatch.setattr(settings, name, value)
    call = Call()
    st = call.state
    await call.event("response.created", response={"id": "r1"})
    await call.event("response.output_item.added", response_id="r1", item={"id": "i1", "type": "message"})
    await call.event("response.output_audio.delta", response_id="r1", delta=AUDIO)
    sent_before_interrupt = len(call.phone.sent)
    await call.event("input_audio_buffer.speech_started")  # the caller cuts in
    await call.event("response.output_audio.delta", response_id="r1", delta=AUDIO)  # late audio of the old answer
    await call.event("response.done", response={"id": "r1", "status": "cancelled"})
    await call.event("response.created", response={"id": "r2"})
    await call.event("response.output_audio.delta", response_id="r2", delta=AUDIO)
    await call.event("response.done", response={"id": "r2", "status": "completed"})

    assert sent_before_interrupt >= 1  # the answer was heard before the interruption
    assert st.hangup_task is None and provider.hangups == []  # nothing in the script asks to end the call
    assert not st.hangup_done and not st.call_ended
    audio_after = [m for m in call.phone.sent[sent_before_interrupt:] if m.get("event") == "playAudio"]
    assert audio_after, "the new answer must reach the caller under every combination"
