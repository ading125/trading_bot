"""FastAPI application factory and lifecycle ownership."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from investing_bot.config import AppSettings, get_settings
from investing_bot.db import Database, JobRunRepository, SocialPostRepository
from investing_bot.logging import configure_logging
from investing_bot.providers import (
    CredentialPresenceStore,
    ProviderManager,
    build_default_registry,
    load_provider_configuration,
)
from investing_bot.web.routes import APP_VERSION, router
from investing_bot.services import CivicTrackerCollector, CivicTrackerPollingService


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
        polling_service: CivicTrackerPollingService | None = None
        app.state.database = database
        app.state.settings = resolved_settings
        try:
            database.connect()
            migration_version = database.migrate()
            repository = JobRunRepository(database)
            post_repository = SocialPostRepository(database)
            interrupted = repository.mark_running_jobs_interrupted()
            app.state.job_runs = repository
            provider_configuration = load_provider_configuration(
                resolved_settings.provider_config_path,
                live_social=resolved_settings.environment != "test",
            )
            provider_manager = ProviderManager(
                registry=build_default_registry(
                    civictracker_member_uuid=resolved_settings.civictracker_member_uuid,
                    civictracker_timeout_seconds=(
                        resolved_settings.civictracker_timeout_seconds
                    ),
                    civictracker_retries=resolved_settings.civictracker_retries,
                ),
                configuration=provider_configuration,
                credentials=CredentialPresenceStore(),
            )
            provider_health = await provider_manager.refresh_health()
            next_poll_at = None
            if resolved_settings.environment != "test":
                next_poll_at = datetime.now(UTC) + timedelta(
                    seconds=resolved_settings.civictracker_poll_seconds
                )
            for health_result in provider_health:
                post_repository.record_health(health_result, next_poll_at=next_poll_at)
            app.state.provider_manager = provider_manager
            app.state.social_posts = post_repository
            collector = CivicTrackerCollector(
                provider_manager=provider_manager,
                posts=post_repository,
                jobs=repository,
                member_uuid=resolved_settings.civictracker_member_uuid,
                page_size=resolved_settings.civictracker_page_size,
                max_pages=resolved_settings.civictracker_max_pages,
            )
            app.state.civictracker_collector = collector
            if (
                resolved_settings.environment != "test"
                and resolved_settings.civictracker_collection_enabled
            ):
                polling_service = CivicTrackerPollingService(
                    collector,
                    interval_seconds=resolved_settings.civictracker_poll_seconds,
                )
                polling_service.start()
            logger.info(
                "application ready",
                extra={
                    "migration_version": migration_version,
                    "interrupted_jobs": interrupted,
                    "provider_health_checks": len(provider_health),
                    "provider_configuration_hash": provider_configuration.configuration_hash,
                },
            )
            yield
        finally:
            if polling_service is not None:
                await polling_service.stop()
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
