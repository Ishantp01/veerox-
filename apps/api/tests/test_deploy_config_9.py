"""Phase 9: the existing AWS deployment is described by files in this repository, and this release must keep working with them.

Nothing here talks to AWS or any host. It reads the workflow, the compose file, the Dockerfile, the proxy config and the migration
chain and fails if a change would break an assumption the automatic deploy relies on (the deploy rebuilds a docker compose stack
over SSH on every push to main, migrates before swapping containers, runs ONE uvicorn process, and sits behind Caddy).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy-aws.yml").read_text(encoding="utf8"))
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf8"))
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf8")
CADDYFILE = (ROOT / "Caddyfile").read_text(encoding="utf8")


def _script() -> str:
    steps = WORKFLOW["jobs"]["deploy"]["steps"]
    return next(s["with"]["script"] for s in steps if "ssh-action" in s.get("uses", ""))


def test_the_deploy_triggers_on_a_push_to_main_and_can_be_run_by_hand() -> None:
    triggers = WORKFLOW.get("on") or WORKFLOW.get(True)  # YAML reads a bare `on` as the boolean True
    assert triggers["push"]["branches"] == ["main"] and "workflow_dispatch" in triggers
    assert WORKFLOW["concurrency"] == {"group": "deploy-aws", "cancel-in-progress": False}  # two pushes never deploy at once


def test_every_compose_command_names_the_production_file() -> None:
    commands = [line.strip() for line in _script().splitlines() if line.strip().startswith("docker compose")]
    assert commands and all(" -f docker-compose.yml " in c for c in commands)  # a bare `docker compose` would pick compose.yml


def test_migrations_run_from_the_new_image_before_the_containers_are_swapped() -> None:
    commands = [line.strip() for line in _script().splitlines() if line.strip().startswith("docker compose")]  # comments excluded
    position = {name: next(i for i, c in enumerate(commands) if needle in c) for name, needle in
                (("build", " build"), ("migrate", "alembic upgrade head"), ("up", " up -d"))}
    assert position["build"] < position["migrate"] < position["up"]
    assert "run --rm --no-deps api" in commands[position["migrate"]]
    assert WORKFLOW["jobs"]["deploy"]["steps"][0]["with"]["script_stop"] is True  # a failed migration leaves the old containers serving


def test_the_deploy_secrets_are_referenced_by_name_only() -> None:
    text = (ROOT / ".github" / "workflows" / "deploy-aws.yml").read_text(encoding="utf8")
    assert set(re.findall(r"secrets\.([A-Z_]+)", text)) == {"AWS_SSH_HOST", "AWS_SSH_USER", "AWS_SSH_KEY", "AWS_DEPLOY_PATH"}


def test_the_api_runs_one_process_on_port_8002_without_reload() -> None:
    cmd = re.search(r'^CMD (\[.*\])', DOCKERFILE, re.M)
    assert cmd is not None
    words = yaml.safe_load(cmd.group(1))
    assert words[:2] == ["uvicorn", "apps.api.main:app"] and words[words.index("--port") + 1] == "8002"
    # Call state (pre-connected AI sessions, live-stream counts) is per process: a second worker would split it.
    assert "--workers" not in words and "--reload" not in words
    assert "EXPOSE 8002" in DOCKERFILE and "USER app" in DOCKERFILE


def test_the_image_carries_what_the_migration_step_needs() -> None:
    assert "COPY migrations/ ./migrations/" in DOCKERFILE and "COPY alembic.ini" in DOCKERFILE
    assert "uv sync --frozen --no-dev" in DOCKERFILE  # test-only packages (fakeredis, lupa) stay out of production


def test_compose_and_the_proxy_agree_on_the_ports_and_the_environment_file() -> None:
    api = COMPOSE["services"]["api"]
    assert api["env_file"] == ".env" and "8002" in [str(p) for p in api["expose"]] and api["restart"] == "unless-stopped"
    assert "ports" not in api  # only Caddy is published
    caddy = COMPOSE["services"]["caddy"]
    assert set(caddy["ports"]) == {"80:80", "443:443"}
    assert re.search(r"api\.workassignai\.com\s*\{\s*reverse_proxy api:8002\s*\}", CADDYFILE)  # WebSocket upgrades pass through reverse_proxy


def test_the_health_endpoints_the_deploy_can_check_exist() -> None:
    from apps.api.main import create_app

    paths = {getattr(r, "path", None) for r in create_app().routes}
    assert {"/health", "/ready", "/voice/answer", "/voice/stream", "/diag/voice-active"} <= paths


def test_there_is_exactly_one_migration_head_and_this_release_adds_none() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1
    git = shutil.which("git")
    if git:
        changed = subprocess.run([git, "-C", str(ROOT), "status", "--porcelain", "--", "migrations"], capture_output=True, text=True).stdout
        assert changed.strip() == "", "a migration changed: it must run on the deploy and be additive"


def test_the_only_required_setting_is_the_one_production_already_has() -> None:
    from apps.api.config import Settings

    assert sorted(n for n, f in Settings.model_fields.items() if f.is_required()) == ["admin_token"]


def test_a_production_environment_starts_with_the_pilot_machinery_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api import pilot
    from apps.api.channels.voice import signature
    from apps.api.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    assert not pilot.pilot_mode() and pilot.workers_enabled() and not pilot.provider_config_changes_blocked("x")
    monkeypatch.setattr(settings, "plivo_signature_mode", "auto")
    assert signature.effective_mode() == "off"
    pilot.check_number_allowed("+919999999999", what="call")  # no allowlist applies
    pilot.reserve_outbound_call("call")  # nor a one-call limit


def test_local_evidence_and_secrets_cannot_be_committed() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf8")
    for pattern in (".env", ".env.pilot", ".validation-*"):
        assert re.search(rf"^{re.escape(pattern)}\s*$", ignored, re.M), pattern


def test_the_dependency_lock_still_matches_the_project_file() -> None:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is not installed here")
    done = subprocess.run([uv, "lock", "--check"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-300:]
