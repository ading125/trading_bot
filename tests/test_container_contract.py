from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).parents[1]


def test_compose_binds_loopback_and_hardens_runtime() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${INVESTING_BOT_HOST_PORT:-8000}:8000"' in compose
    assert "read_only: true" in compose
    assert "cap_drop:\n      - ALL" in compose
    assert "no-new-privileges:true" in compose
    assert "investing_bot_data:/data" in compose
    assert "/tmp:size=64m,mode=1777" in compose


def test_docker_runtime_is_non_root_and_lock_is_exact() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")

    assert "USER investingbot:investingbot" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert "requirements.lock" in dockerfile
    dependency_lines = [
        line for line in lock.splitlines() if line and not line.startswith("#")
    ]
    assert dependency_lines
    assert all(re.fullmatch(r"[A-Za-z0-9_.-]+==[^=\s]+", line) for line in dependency_lines)
