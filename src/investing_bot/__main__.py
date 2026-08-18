"""Production entry point for the local service."""

from __future__ import annotations

import uvicorn

from investing_bot.app import create_app
from investing_bot.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=str(settings.bind_host),
        port=settings.port,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
