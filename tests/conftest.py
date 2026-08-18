from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run async API tests on the production asyncio backend only."""

    return "asyncio"
