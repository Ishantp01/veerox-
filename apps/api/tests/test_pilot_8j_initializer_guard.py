"""Phase 8J: the replay-state initializer must write only to the pilot file's own Redis.

A process environment variable beats the env file, so a stray REDIS_URL in the shell would have pointed the initializer at
some other Redis and still passed its old "differs from the main .env" test. No Redis is contacted here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import pilot_security_initialize as init

PILOT = {
    "ENVIRONMENT": "pilot", "REDIS_URL": "rediss://d:p@pilot.example.net:6379", "SECRET_ENCRYPTION_KEY": "k",
    "ADMIN_TOKEN": "a", "PLIVO_AUTH_TOKEN": "t",
}
MAIN = {"REDIS_URL": "rediss://d:p@main.example.net:6379"}


def settings(**changes):
    values = dict(environment="pilot", redis_url=PILOT["REDIS_URL"], secret_encryption_key="k", admin_token="a", plivo_auth_token="t")
    values.update(changes)
    return SimpleNamespace(**values)


def test_the_pilot_files_own_values_are_accepted() -> None:
    init.verify_pilot_authority(settings(), PILOT, MAIN)


@pytest.mark.parametrize(
    "change",
    [
        {"redis_url": "rediss://d:p@some-other-redis.example.net:6379"},  # a shell REDIS_URL, not the main one
        {"redis_url": "rediss://d:p@pilot.example.net:6380"},
        {"environment": "production"},
        {"secret_encryption_key": "other"},  # would initialise a different namespace
        {"admin_token": "other"},
        {"plivo_auth_token": "other"},
    ],
)
def test_any_override_of_the_pilot_file_is_refused(change: dict) -> None:
    with pytest.raises(RuntimeError, match="override"):
        init.verify_pilot_authority(settings(**change), PILOT, MAIN)


def test_the_main_environments_redis_is_refused_even_if_the_pilot_file_names_it() -> None:
    same = {**PILOT, "REDIS_URL": MAIN["REDIS_URL"]}
    with pytest.raises(RuntimeError, match="isolated_pilot_redis_required"):
        init.verify_pilot_authority(settings(redis_url=MAIN["REDIS_URL"]), same, MAIN)


def test_a_pilot_file_without_a_redis_is_refused() -> None:
    with pytest.raises(RuntimeError, match="pilot_redis_missing"):
        init.verify_pilot_authority(settings(redis_url=""), {**PILOT, "REDIS_URL": ""}, MAIN)


def test_confirmations_are_required_by_the_command_line_and_nothing_runs_on_import() -> None:
    import subprocess
    import sys
    from pathlib import Path

    script = Path(init.__file__)
    done = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode != 0 and "required" in done.stderr  # argparse refuses before any connection is attempted
