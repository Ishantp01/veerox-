"""Run pytest with synthetic credentials, local temp files, and no external sockets.

Default: complete API suite. Pass test paths to run selected offline simulations.
This affects only this Python process; no env file or application setting is changed.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["apps/api/tests"])
    parser.add_argument("--basetemp", default=".validation-8d/tmp-api")
    parser.add_argument("--junitxml", default=".validation-8d/api.xml")
    args = parser.parse_args()
    os.chdir(ROOT)
    for name in (args.basetemp, args.junitxml):
        if not (ROOT / name).resolve().is_relative_to(ROOT):
            parser.error("test artifacts must stay inside the workspace")
    (ROOT / args.basetemp).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / args.junitxml).parent.mkdir(parents=True, exist_ok=True)
    # Discover settings names without importing config (which would load .env).
    module = ast.parse((ROOT / "apps/api/config.py").read_text(encoding="utf-8"))
    for node in ast.walk(module):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            os.environ.pop(node.target.id.upper(), None)
    os.environ.update(
        VEEROX_ENV_FILE=str(ROOT / ".validation-8d/no-runtime-secrets.env"),
        ADMIN_TOKEN="offline-validation-admin",
        DATABASE_URL="postgresql+asyncpg://offline:offline@127.0.0.1:9/offline_test",
        REDIS_URL="redis://127.0.0.1:9/0",
        TEST_DATABASE_URL="sqlite+aiosqlite:///:memory:",
        AWS_EC2_METADATA_DISABLED="true",
    )
    from cryptography.fernet import Fernet

    os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    def network_guard(event: str, args: tuple) -> None:
        if event == "socket.connect":
            address = args[1]
            if isinstance(address, tuple) and str(address[0]) not in (
                "127.0.0.1",
                "::1",
                "localhost",
            ):
                raise PermissionError("Offline validation blocks external network connections")

    sys.addaudithook(network_guard)
    import pytest

    return pytest.main(
        [
            *(args.paths or ["apps/api/tests"]),
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={args.basetemp}",
            f"--junitxml={args.junitxml}",
            "--tb=short",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
