from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "src" / "investing_bot"
PROVIDER_ROOT = SOURCE / "providers"
PROVIDER_SDKS = frozenset(
    {
        "anthropic",
        "cloudflare",
        "groq",
        "openai",
        "yfinance",
    }
)


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.partition(".")[0])
    return imports


def test_provider_sdks_cannot_leak_outside_adapter_package() -> None:
    violations: list[str] = []
    for path in SOURCE.rglob("*.py"):
        if path.is_relative_to(PROVIDER_ROOT):
            continue
        leaked = imported_roots(path) & PROVIDER_SDKS
        if leaked:
            violations.append(f"{path.relative_to(ROOT)}: {sorted(leaked)}")

    assert not violations, "provider SDK imports outside adapters:\n" + "\n".join(
        violations
    )
