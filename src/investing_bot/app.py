"""FastAPI application factory and lifecycle ownership."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from investing_bot.config import AppSettings, get_settings
from investing_bot.db import (
    AnalysisRepository,
    BacktestRepository,
    CandidateRepository,
    Database,
    JobRunRepository,
    MarketDataRepository,
    OperationsRepository,
    SocialPostRepository,
    StrategyRepository,
)
from investing_bot.models import ManualAction
from investing_bot.logging import configure_logging
from investing_bot.providers import (
    CredentialReferenceStore,
    EncryptedCredentialStore,
    ProviderManager,
    build_default_registry,
    load_provider_configuration,
)
from investing_bot.web.routes import APP_VERSION, router
from investing_bot.services import (
    AnalysisEvidenceBuilder,
    BacktestResearchService,
    CandidateRegistryService,
    CivicTrackerCollector,
    CompanyResolver,
    GrowthAnalysisService,
    MarketDataCollector,
    MarketPollingService,
    OperationalScheduler,
    OperationsCoordinator,
    OperationsSchedulePlanner,
    OutcomeTrackingService,
    SP500UniverseCollector,
    StrategyEvaluationService,
)
from investing_bot.strategies import build_default_strategy_registry


logger = logging.getLogger(__name__)
_WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app(
    settings: AppSettings | None = None,
    *,
    credentials: CredentialReferenceStore | None = None,
) -> FastAPI:
    """Create an isolated application instance for production or tests."""

    resolved_settings = settings or get_settings()
    credential_store = credentials or EncryptedCredentialStore(
        resolved_settings.credential_vault_path
    )
    configure_logging(
        level=resolved_settings.log_level,
        log_format=resolved_settings.log_format,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(resolved_settings.database_path)
        repository: JobRunRepository | None = None
        provider_manager: ProviderManager | None = None
        operational_scheduler: OperationalScheduler | None = None
        app.state.database = database
        app.state.settings = resolved_settings
        try:
            database.connect()
            migration_version = database.migrate()
            repository = JobRunRepository(database)
            post_repository = SocialPostRepository(database)
            market_repository = MarketDataRepository(
                database, dataset_root=resolved_settings.market_dataset_path
            )
            candidate_repository = CandidateRepository(database)
            analysis_repository = AnalysisRepository(database)
            strategy_repository = StrategyRepository(database)
            backtest_repository = BacktestRepository(database)
            operations_repository = OperationsRepository(database)
            strategy_registry = build_default_strategy_registry()
            interrupted = repository.mark_running_jobs_interrupted()
            app.state.job_runs = repository
            provider_configuration = load_provider_configuration(
                resolved_settings.provider_config_path,
                live_social=resolved_settings.environment != "test",
                live_market=resolved_settings.environment != "test",
                live_analysis=resolved_settings.environment != "test",
            )
            provider_manager = ProviderManager(
                registry=build_default_registry(
                    civictracker_member_uuid=resolved_settings.civictracker_member_uuid,
                    civictracker_timeout_seconds=(
                        resolved_settings.civictracker_timeout_seconds
                    ),
                    civictracker_retries=resolved_settings.civictracker_retries,
                    yahoo_raw_cache_dir=resolved_settings.market_raw_cache_path,
                    yahoo_repair=resolved_settings.yahoo_repair_enabled,
                    yahoo_timeout_seconds=resolved_settings.yahoo_timeout_seconds,
                    yahoo_retries=resolved_settings.yahoo_retries,
                    credentials=credential_store,
                    groq_timeout_seconds=resolved_settings.groq_timeout_seconds,
                    groq_retries=resolved_settings.groq_retries,
                ),
                configuration=provider_configuration,
                credentials=credential_store,
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
            app.state.market_data = market_repository
            app.state.candidates = candidate_repository
            app.state.analyses = analysis_repository
            app.state.strategy_evaluations = strategy_repository
            app.state.strategy_registry = strategy_registry
            app.state.backtests = backtest_repository
            app.state.operation_runs = operations_repository
            collector = CivicTrackerCollector(
                provider_manager=provider_manager,
                posts=post_repository,
                jobs=repository,
                member_uuid=resolved_settings.civictracker_member_uuid,
                page_size=resolved_settings.civictracker_page_size,
                max_pages=resolved_settings.civictracker_max_pages,
            )
            app.state.civictracker_collector = collector
            market_collector = MarketDataCollector(
                provider_manager=provider_manager,
                repository=market_repository,
                jobs=repository,
            )
            app.state.market_collector = market_collector
            sp500_collector = SP500UniverseCollector(market_repository)
            app.state.sp500_collector = sp500_collector
            company_resolver = CompanyResolver(
                repository=candidate_repository,
                provider_manager=provider_manager,
            )
            candidate_service = CandidateRegistryService(
                repository=candidate_repository,
                resolver=company_resolver,
                jobs=repository,
            )
            app.state.candidate_service = candidate_service
            analysis_service = GrowthAnalysisService(
                evidence_builder=AnalysisEvidenceBuilder(candidate_repository),
                repository=analysis_repository,
                provider_manager=provider_manager,
                jobs=repository,
            )
            app.state.analysis_service = analysis_service
            strategy_service = StrategyEvaluationService(
                market=market_repository,
                candidates=candidate_repository,
                analyses=analysis_repository,
                strategies=strategy_registry,
                repository=strategy_repository,
                jobs=repository,
            )
            app.state.strategy_service = strategy_service
            app.state.backtest_service = BacktestResearchService(
                market=market_repository,
                strategies=strategy_registry,
                repository=backtest_repository,
                jobs=repository,
            )
            market_poller = MarketPollingService(
                market_collector,
                symbols=resolved_settings.parsed_market_seed_symbols,
                interval_seconds=resolved_settings.market_poll_seconds,
                daily_history_days=resolved_settings.market_daily_history_days,
                intraday_history_days=resolved_settings.market_intraday_history_days,
                universe_collector=sp500_collector,
            )
            outcome_service = OutcomeTrackingService(
                analyses=analysis_repository,
                market=market_repository,
            )
            scheduled_actions: set[ManualAction] = {ManualAction.OUTCOMES}
            if resolved_settings.civictracker_collection_enabled:
                scheduled_actions.add(ManualAction.SOURCES)
            if resolved_settings.market_collection_enabled:
                scheduled_actions.add(ManualAction.MARKET)
            if resolved_settings.candidate_refresh_enabled:
                scheduled_actions.add(ManualAction.CANDIDATES)
            if resolved_settings.analysis_refresh_enabled:
                scheduled_actions.add(ManualAction.ANALYSIS)
            if (
                resolved_settings.strategy_refresh_enabled
                and resolved_settings.parsed_strategy_seed_symbols
            ):
                scheduled_actions.add(ManualAction.STRATEGIES)
            operations = OperationsCoordinator(
                jobs=repository,
                config_hash=provider_configuration.configuration_hash,
                source_collector=collector,
                market_poller=market_poller,
                candidate_service=candidate_service,
                analysis_service=analysis_service,
                strategy_service=strategy_service,
                outcome_service=outcome_service,
                analysis_symbols=resolved_settings.parsed_analysis_seed_symbols,
                analysis_candidate_limit=resolved_settings.analysis_candidate_limit,
                strategy_symbols=resolved_settings.parsed_strategy_seed_symbols,
                scheduled_actions=frozenset(scheduled_actions),
            )
            schedule_planner = OperationsSchedulePlanner()
            app.state.outcome_service = outcome_service
            app.state.operations = operations
            app.state.schedule_planner = schedule_planner
            if resolved_settings.environment != "test":
                operational_scheduler = OperationalScheduler(
                    planner=schedule_planner,
                    repository=operations_repository,
                    handler=operations.run_scheduled,
                )
                operational_scheduler.start()
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
            if operational_scheduler is not None:
                await operational_scheduler.stop()
            if repository is not None:
                interrupted = repository.mark_running_jobs_interrupted()
                if interrupted:
                    logger.warning(
                        "running jobs interrupted during shutdown",
                        extra={"interrupted_jobs": interrupted},
                    )
            if provider_manager is not None:
                await provider_manager.close()
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
    app.state.csrf_token = token_urlsafe(32)

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
        if request.url.path.startswith("/api/") or request.url.path == "/":
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
