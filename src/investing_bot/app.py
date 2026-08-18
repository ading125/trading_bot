"""FastAPI application factory and lifecycle ownership."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from investing_bot.config import AppSettings, get_settings
from investing_bot.db import Database, JobRunRepository
from investing_bot.logging import configure_logging
from investing_bot.web.routes import APP_VERSION, router


logger = logging.getLogger(__name__)
_WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Create an isolated application instance for production or tests."""

    resolved_settings = settings or get_settings()
    configure_logging(
        level=resolved_settings.log_level,
        log_format=resolved_settings.log_format,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(resolved_settings.database_path)
        repository: JobRunRepository | None = None
        app.state.database = database
        app.state.settings = resolved_settings
        try:
            database.connect()
            migration_version = database.migrate()
            repository = JobRunRepository(database)
            interrupted = repository.mark_running_jobs_interrupted()
            app.state.job_runs = repository
            logger.info(
                "application ready",
                extra={
                    "migration_version": migration_version,
                    "interrupted_jobs": interrupted,
                },
            )
            yield
        finally:
            if repository is not None:
                interrupted = repository.mark_running_jobs_interrupted()
                if interrupted:
                    logger.warning(
                        "running jobs interrupted during shutdown",
                        extra={"interrupted_jobs": interrupted},
                    )
            database.close()
            logger.info("application stopped")

    app = FastAPI(
        title="Investing Bot",
        summary="Private, local stock-research application",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def security_and_request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid4())
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["content-security-policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["cache-control"] = "no-store"
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )
        return response

    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
    app.include_router(router)
    return app
