"""Read-only foundation routes for health and the dashboard shell."""

from __future__ import annotations

from importlib.metadata import version
from datetime import UTC, date, datetime
import hmac
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from investing_bot.models import (
    BacktestRequest,
    BarInterval,
    ManualAction,
    ManualActionResult,
    OperationalScheduleRun,
    ScheduledOperation,
    PriceAdjustment,
    ProviderCapability,
    StrategyManifest,
    WalkForwardExperimentRequest,
)
from investing_bot.providers.contracts import (
    ConnectionTestResult,
    ProviderManifest,
)
from investing_bot.providers.manager import ProviderManager
from investing_bot.db import (
    AIAssessment,
    AnalysisEvidencePackage,
    AnalysisOutcome,
    BacktestRunSummary,
    Candidate,
    CandidateEvidence,
    EntityResolution,
    MarketDataset,
    MarketStatus,
    ResolutionStatus,
    StoredBacktestExperiment,
    StoredBacktestRun,
    StoredSocialPost,
    StoredStrategyEvaluation,
)
from investing_bot.services import (
    BacktestExecution,
    BacktestResearchError,
    ExperimentExecution,
    ManualActionBusyError,
    ManualActionRateLimitError,
    SanitizedDiagnostics,
    build_sanitized_diagnostics,
)


APP_VERSION = version("investing-bot")
_WEB_DIR = Path(__file__).resolve().parent
_INLINE_SOURCE_CITATION = re.compile(
    r"\s*(?:\(|\[)?source(?:_id)?\s*[:#]?\s*[0-9a-f]{64}(?:\)|\])?",
    re.IGNORECASE,
)


def _humanize_analysis_text(value: object) -> str:
    return _INLINE_SOURCE_CITATION.sub("", str(value)).strip()


templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
templates.env.filters["humanize_analysis_text"] = _humanize_analysis_text

router = APIRouter()


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    service: str
    version: str


class ReadinessResponse(HealthResponse):
    migration_version: int | None


class ProviderDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration_hash: str
    providers: tuple[ProviderManifest, ...]
    selections: dict[ProviderCapability, "CapabilitySelectionResponse"]


class ProviderTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str
    credential_required: bool
    credential_present: bool
    credential_configured: bool


class CapabilitySelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: ProviderTargetResponse
    fallbacks: tuple[ProviderTargetResponse, ...]


class ProviderHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: tuple[ConnectionTestResult, ...]


class CredentialStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initialized: bool
    unlocked: bool
    configured_references: int = Field(ge=0)


class SocialPostsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: tuple[StoredSocialPost, ...]
    count: int = Field(ge=0)


class StoredSourceHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str
    capability: str
    state: str
    checked_at: AwareDatetime
    last_success_at: AwareDatetime | None
    error_code: str | None
    message: str | None
    latency_ms: float
    consecutive_failures: int
    next_poll_at: AwareDatetime | None


class StoredSourceHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: tuple[StoredSourceHealth, ...]


class StoredMarketBar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    interval: str
    adjustment: str
    bar_start: AwareDatetime
    bar_end: AwareDatetime
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    provider_id: str
    repaired: bool


class MarketBarsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[StoredMarketBar, ...]
    count: int = Field(ge=0)


class MarketDatasetsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[MarketDataset, ...]
    count: int = Field(ge=0)


class CandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[Candidate, ...]
    count: int = Field(ge=0)


class CandidateEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[CandidateEvidence, ...]
    count: int = Field(ge=0)


class EntityResolutionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[EntityResolution, ...]
    count: int = Field(ge=0)


class AssessmentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[AIAssessment, ...]
    count: int = Field(ge=0)


class AnalysisOutcomesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[AnalysisOutcome, ...]
    count: int = Field(ge=0)


class StrategyRegistryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[StrategyManifest, ...]
    count: int = Field(ge=0)


class StrategyEvaluationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[StoredStrategyEvaluation, ...]
    count: int = Field(ge=0)


class BacktestRunsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[BacktestRunSummary, ...]
    count: int = Field(ge=0)


class BacktestExperimentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[StoredBacktestExperiment, ...]
    count: int = Field(ge=0)


class OperationsScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[ScheduledOperation, ...]


class OperationsRunsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[OperationalScheduleRun, ...]


class ProviderOperationsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    provider_id: str
    display_name: str
    supported_capabilities: str
    selection_role: str
    active_selection: bool
    fallback_active: bool
    state: str
    last_success_at: AwareDatetime | None
    freshness_seconds: int | None
    latency_ms: float | None
    quota: str
    contract_status: str
    schema_version: str
    adapter_version: str
    rate_limit: str


class ProviderOperationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[ProviderOperationsView, ...]


class AlertView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation: StoredStrategyEvaluation
    assessment: AIAssessment | None


class AlertsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[AlertView, ...]
    count: int = Field(ge=0)


@router.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Process-liveness endpoint that does not depend on external services."""

    return HealthResponse(
        status="ok", service="investing-bot", version=APP_VERSION
    )


@router.get(
    "/api/v1/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def readiness(request: Request) -> ReadinessResponse | JSONResponse:
    """Report ready only after the expected database migrations are healthy."""

    database = getattr(request.app.state, "database", None)
    ready = database is not None and database.is_ready()
    response = ReadinessResponse(
        status="ready" if ready else "not_ready",
        service="investing-bot",
        version=APP_VERSION,
        migration_version=(database.latest_migration if database else None),
    )
    if ready:
        return response
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=response.model_dump(mode="json"),
    )


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render the first read-only dashboard shell."""

    database = getattr(request.app.state, "database", None)
    ready = database is not None and database.is_ready()
    provider_manager = getattr(request.app.state, "provider_manager", None)
    provider_health = (
        provider_manager.health_snapshot() if provider_manager is not None else ()
    )
    analysis_mode = _analysis_mode_view(provider_manager, provider_health)
    healthy_providers = len(
        {item.provider_id for item in provider_health if item.available}
    )
    market_status = (
        request.app.state.market_data.status()
        if hasattr(request.app.state, "market_data")
        else None
    )
    candidates = (
        request.app.state.candidates.list_candidates(limit=20)
        if hasattr(request.app.state, "candidates")
        else []
    )
    analyses = (
        request.app.state.analyses.list_latest(limit=50)
        if hasattr(request.app.state, "analyses")
        else []
    )
    analyses.sort(
        key=lambda item: (item.growth_score, item.evidence_quality, item.created_at),
        reverse=True,
    )
    analysis_views = [
        {
            "rank": index,
            "assessment": assessment,
            "momentum": (
                request.app.state.market_data.momentum(assessment.ticker)
                if hasattr(request.app.state, "market_data")
                else None
            ),
        }
        for index, assessment in enumerate(analyses, start=1)
    ]
    analysis_evidence_counts = {}
    if hasattr(request.app.state, "analyses"):
        for assessment in analyses:
            package = request.app.state.analyses.get_package(assessment.evidence_hash)
            analysis_evidence_counts[assessment.evidence_hash] = (
                len(package.evidence) if package is not None else 0
            )
    strategy_manifests = (
        request.app.state.strategy_registry.manifests()
        if hasattr(request.app.state, "strategy_registry")
        else ()
    )
    strategy_evaluations = (
        request.app.state.strategy_evaluations.list_latest(limit=20)
        if hasattr(request.app.state, "strategy_evaluations")
        else []
    )
    backtest_runs = (
        request.app.state.backtests.list_run_summaries(limit=5)
        if hasattr(request.app.state, "backtests")
        else []
    )
    backtest_experiments = (
        request.app.state.backtests.list_experiments(limit=5)
        if hasattr(request.app.state, "backtests")
        else []
    )
    credentials = (
        provider_manager.credentials if provider_manager is not None else None
    )
    credential_status = (
        credentials.status()
        if credentials is not None and hasattr(credentials, "status")
        else None
    )
    alerts = [
        {
            "evaluation": evaluation,
            "assessment": request.app.state.analyses.get(evaluation.assessment_id),
            "chart": _price_chart(request, evaluation.signal.symbol),
        }
        for evaluation in strategy_evaluations
    ]
    schedule = (
        request.app.state.schedule_planner.next_runs()
        if hasattr(request.app.state, "schedule_planner")
        else ()
    )
    operation_runs = (
        request.app.state.operation_runs.list_recent(limit=12)
        if hasattr(request.app.state, "operation_runs")
        else ()
    )
    provider_operations = _provider_operations_view(request)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "ready": ready,
            "migration_version": database.latest_migration if database else None,
            "version": APP_VERSION,
            "provider_health": provider_health,
            "analysis_mode": analysis_mode,
            "healthy_providers": healthy_providers,
            "market_status": market_status,
            "candidates": candidates,
            "candidate_count": (
                request.app.state.candidates.count_candidates()
                if hasattr(request.app.state, "candidates")
                else 0
            ),
            "analyses": analyses,
            "analysis_views": analysis_views,
            "analysis_evidence_counts": analysis_evidence_counts,
            "analysis_count": (
                request.app.state.analyses.count()
                if hasattr(request.app.state, "analyses")
                else 0
            ),
            "strategy_manifests": strategy_manifests,
            "strategy_evaluations": strategy_evaluations,
            "alerts": alerts,
            "strategy_evaluation_count": (
                request.app.state.strategy_evaluations.count()
                if hasattr(request.app.state, "strategy_evaluations")
                else 0
            ),
            "backtest_runs": backtest_runs,
            "backtest_experiments": backtest_experiments,
            "backtest_run_count": (
                request.app.state.backtests.run_count()
                if hasattr(request.app.state, "backtests")
                else 0
            ),
            "backtest_experiment_count": (
                request.app.state.backtests.experiment_count()
                if hasattr(request.app.state, "backtests")
                else 0
            ),
            "credential_status": credential_status,
            "social_posts": (
                request.app.state.social_posts.list_posts(limit=20)
                if hasattr(request.app.state, "social_posts")
                else []
            ),
            "social_post_count": (
                request.app.state.social_posts.count_posts()
                if hasattr(request.app.state, "social_posts")
                else 0
            ),
            "source_health": (
                request.app.state.social_posts.list_health()
                if hasattr(request.app.state, "social_posts")
                else []
            ),
            "schedule": schedule,
            "operation_runs": operation_runs,
            "provider_operations": provider_operations,
            "operation_notice": request.query_params.get("operation"),
            "operation_state": request.query_params.get("state"),
            "csrf_token": request.app.state.csrf_token,
        },
    )


def _price_chart(request: Request, symbol: str) -> tuple[dict[str, object], ...]:
    rows = request.app.state.market_data.list_bars(
        symbol=symbol,
        interval=BarInterval.DAY_1,
        adjustment=PriceAdjustment.ADJUSTED,
        limit=48,
    )
    rows.reverse()
    closes = [float(row["close"]) for row in rows]
    if not closes:
        return ()
    low, high = min(closes), max(closes)
    span = high - low
    return tuple(
        {
            "date": row["session_date"],
            "close": float(row["close"]),
            "level": (
                3
                if span == 0
                else min(10, 1 + int(9 * (float(row["close"]) - low) / span))
            ),
        }
        for row in rows
    )


def _provider_operations_view(request: Request) -> tuple[ProviderOperationsView, ...]:
    manager = getattr(request.app.state, "provider_manager", None)
    if manager is None:
        return ()
    now = datetime.now(UTC)
    health = {
        (item.provider_id, item.capability): item
        for item in manager.health_snapshot()
    }
    views: list[ProviderOperationsView] = []
    for capability, selection in manager.configuration.selections.items():
        available = next(
            (
                target.provider_id
                for target in selection.ordered_targets
                if (target.provider_id, capability) in health
                and health[(target.provider_id, capability)].available
            ),
            None,
        )
        for index, target in enumerate(selection.ordered_targets):
            manifest = manager.registry.manifest(target.provider_id)
            result = health.get((target.provider_id, capability))
            last_success = (
                result.checked_at if result is not None and result.available else None
            )
            freshness = (
                max(0, int((now - last_success).total_seconds()))
                if last_success is not None
                else None
            )
            quota = "not reported"
            if result is not None and result.quota is not None:
                remaining = (
                    "?" if result.quota.remaining is None else str(result.quota.remaining)
                )
                limit = "?" if result.quota.limit is None else str(result.quota.limit)
                quota = f"{remaining} / {limit} remaining"
            rate_limit = "not declared"
            if manifest.rate_limit is not None:
                rate_limit = (
                    f"{manifest.rate_limit.requests} requests / "
                    f"{manifest.rate_limit.window_seconds}s"
                )
            views.append(
                ProviderOperationsView(
                    capability=capability.value,
                    provider_id=target.provider_id,
                    display_name=manifest.display_name,
                    supported_capabilities=", ".join(
                        sorted(item.value for item in manifest.capabilities)
                    ),
                    selection_role="primary" if index == 0 else f"fallback {index}",
                    active_selection=target.provider_id == available,
                    fallback_active=index > 0 and target.provider_id == available,
                    state="unknown" if result is None else result.state.value,
                    last_success_at=last_success,
                    freshness_seconds=freshness,
                    latency_ms=None if result is None else result.latency_ms,
                    quota=quota,
                    contract_status=(
                        "compatible"
                        if capability in manifest.schema_versions
                        else "incompatible"
                    ),
                    schema_version=manifest.schema_versions[capability],
                    adapter_version=manifest.adapter_version,
                    rate_limit=rate_limit,
                )
            )
    return tuple(views)


def _analysis_mode_view(
    manager: ProviderManager | None,
    health_results: tuple[ConnectionTestResult, ...],
) -> dict[str, str]:
    """Describe the structured-analysis provider that would be selected now."""

    unavailable = {
        "state": "unavailable",
        "title": "AI ANALYSIS UNAVAILABLE",
        "detail": "No configured structured-analysis provider is currently available.",
        "provider": "No provider ready",
    }
    if manager is None:
        return unavailable
    selection = manager.configuration.selections.get(
        ProviderCapability.STRUCTURED_LLM
    )
    if selection is None:
        return unavailable
    health_by_provider = {
        item.provider_id: item
        for item in health_results
        if item.capability is ProviderCapability.STRUCTURED_LLM
    }
    for target in selection.ordered_targets:
        health = health_by_provider.get(target.provider_id)
        if health is None or not health.available:
            continue
        manifest = manager.registry.manifest(target.provider_id)
        model = manifest.model_id or manifest.adapter_version
        provider = f"{manifest.provider_id} · {model}"
        if manifest.fixture:
            return {
                "state": "offline",
                "title": "OFFLINE ANALYSIS — RECORDED FALLBACK",
                "detail": (
                    "New assessments use deterministic recorded output. "
                    "No analysis evidence is sent to a hosted AI."
                ),
                "provider": provider,
            }
        return {
            "state": "live",
            "title": "LIVE AI ACTIVE",
            "detail": (
                "New assessments use the hosted model with bounded, "
                "source-attributed evidence."
            ),
            "provider": provider,
        }
    return unavailable


@router.get("/api/v1/providers", response_model=ProviderDiscoveryResponse)
async def providers(request: Request) -> ProviderDiscoveryResponse:
    manager = request.app.state.provider_manager
    selections: dict[ProviderCapability, CapabilitySelectionResponse] = {}
    for capability, selection in manager.configuration.selections.items():
        targets: list[ProviderTargetResponse] = []
        for target in selection.ordered_targets:
            manifest = manager.registry.manifest(target.provider_id)
            configured = bool(
                target.credential_ref is not None
                and manager.credentials.is_configured(target.credential_ref)
            )
            present = bool(
                target.credential_ref is not None
                and manager.credentials.has_reference(target.credential_ref)
            )
            targets.append(
                ProviderTargetResponse(
                    provider_id=target.provider_id,
                    credential_required=manifest.authentication_required,
                    credential_present=present,
                    credential_configured=configured,
                )
            )
        selections[capability] = CapabilitySelectionResponse(
            primary=targets[0], fallbacks=tuple(targets[1:])
        )
    return ProviderDiscoveryResponse(
        configuration_hash=manager.configuration.configuration_hash,
        providers=manager.registry.manifests(),
        selections=selections,
    )


@router.get("/api/v1/providers/health", response_model=ProviderHealthResponse)
async def provider_health(request: Request) -> ProviderHealthResponse:
    manager = request.app.state.provider_manager
    return ProviderHealthResponse(results=manager.health_snapshot())


@router.get(
    "/api/v1/providers/operations", response_model=ProviderOperationsResponse
)
async def provider_operations(request: Request) -> ProviderOperationsResponse:
    return ProviderOperationsResponse(items=_provider_operations_view(request))


@router.get(
    "/api/v1/operations/schedule", response_model=OperationsScheduleResponse
)
async def operations_schedule(request: Request) -> OperationsScheduleResponse:
    return OperationsScheduleResponse(
        items=request.app.state.schedule_planner.next_runs()
    )


@router.get(
    "/api/v1/operations/runs", response_model=OperationsRunsResponse
)
async def operations_runs(
    request: Request, limit: int = Query(default=50, ge=1, le=500)
) -> OperationsRunsResponse:
    return OperationsRunsResponse(
        items=tuple(request.app.state.operation_runs.list_recent(limit=limit))
    )


@router.post(
    "/api/v1/operations/refresh/{action}", response_model=ManualActionResult
)
async def manual_operation_api(
    request: Request, action: str
) -> ManualActionResult:
    _require_csrf_token(request, request.headers.get("x-csrf-token"))
    selected = _manual_action(action)
    try:
        return await request.app.state.operations.run_manual(selected)
    except ManualActionRateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except ManualActionBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/actions/refresh/{action}", response_class=RedirectResponse)
async def manual_operation_form(request: Request, action: str) -> RedirectResponse:
    await _require_form_csrf_token(request)
    selected = _manual_action(action)
    state_value = "completed"
    try:
        await request.app.state.operations.run_manual(selected)
    except ManualActionRateLimitError:
        state_value = "rate_limited"
    except ManualActionBusyError:
        state_value = "busy"
    except Exception:
        state_value = "failed"
    fragment = "#analysis-heading" if selected is ManualAction.ANALYSIS else ""
    return RedirectResponse(
        url=f"/?operation={selected.value}&state={state_value}{fragment}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/api/v1/diagnostics", response_model=SanitizedDiagnostics)
async def diagnostics(request: Request) -> SanitizedDiagnostics:
    credentials = request.app.state.provider_manager.credentials
    vault_status = (
        credentials.status()
        if hasattr(credentials, "status")
        else SimpleNamespace(
            initialized=False,
            unlocked=credentials.unlocked,
            references=(),
        )
    )
    return build_sanitized_diagnostics(
        settings=request.app.state.settings,
        database=request.app.state.database,
        jobs=request.app.state.job_runs,
        provider_manager=request.app.state.provider_manager,
        vault_status=vault_status,
    )


@router.get("/api/v1/credentials/status", response_model=CredentialStatusResponse)
async def credential_status(request: Request) -> CredentialStatusResponse:
    credentials = request.app.state.provider_manager.credentials
    if hasattr(credentials, "status"):
        current = credentials.status()
        return CredentialStatusResponse(
            initialized=current.initialized,
            unlocked=current.unlocked,
            configured_references=len(current.references),
        )
    return CredentialStatusResponse(
        initialized=False,
        unlocked=credentials.unlocked,
        configured_references=0,
    )


@router.get(
    "/api/v1/sources/civictracker/posts", response_model=SocialPostsResponse
)
async def civictracker_posts(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> SocialPostsResponse:
    items = tuple(request.app.state.social_posts.list_posts(limit=limit))
    return SocialPostsResponse(items=items, count=len(items))


@router.get(
    "/api/v1/sources/health", response_model=StoredSourceHealthResponse
)
async def source_health(request: Request) -> StoredSourceHealthResponse:
    items = tuple(
        StoredSourceHealth.model_validate(item)
        for item in request.app.state.social_posts.list_health()
    )
    return StoredSourceHealthResponse(results=items)


@router.get("/api/v1/market/status", response_model=MarketStatus)
async def market_status(request: Request) -> MarketStatus:
    return request.app.state.market_data.status()


@router.get("/api/v1/market/bars", response_model=MarketBarsResponse)
async def market_bars(
    request: Request,
    symbol: str = Query(pattern=r"^[A-Z][A-Z0-9.-]{0,11}$"),
    interval: BarInterval = BarInterval.DAY_1,
    adjustment: PriceAdjustment = PriceAdjustment.ADJUSTED,
    limit: int = Query(default=200, ge=1, le=5_000),
) -> MarketBarsResponse:
    rows = request.app.state.market_data.list_bars(
        symbol=symbol,
        interval=interval,
        adjustment=adjustment,
        limit=limit,
    )
    items = tuple(StoredMarketBar.model_validate(row) for row in rows)
    return MarketBarsResponse(items=items, count=len(items))


@router.get("/api/v1/market/datasets", response_model=MarketDatasetsResponse)
async def market_datasets(request: Request) -> MarketDatasetsResponse:
    items = tuple(request.app.state.market_data.list_datasets())
    return MarketDatasetsResponse(items=items, count=len(items))


@router.get("/api/v1/candidates", response_model=CandidatesResponse)
async def candidates(
    request: Request,
    active_only: bool = True,
    limit: int = Query(default=200, ge=1, le=500),
) -> CandidatesResponse:
    items = tuple(
        request.app.state.candidates.list_candidates(
            active_only=active_only, limit=limit
        )
    )
    return CandidatesResponse(items=items, count=len(items))


@router.get(
    "/api/v1/candidates/{symbol}/evidence",
    response_model=CandidateEvidenceResponse,
)
async def candidate_evidence(
    request: Request,
    symbol: str,
    active_only: bool = True,
) -> CandidateEvidenceResponse:
    normalized = symbol.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", normalized):
        raise HTTPException(status_code=422, detail="invalid ticker symbol")
    items = tuple(
        request.app.state.candidates.list_evidence(
            symbol=normalized, active_only=active_only
        )
    )
    return CandidateEvidenceResponse(items=items, count=len(items))


@router.get("/api/v1/resolutions", response_model=EntityResolutionsResponse)
async def entity_resolutions(
    request: Request,
    resolution_status: ResolutionStatus | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> EntityResolutionsResponse:
    items = tuple(
        request.app.state.candidates.list_resolutions(
            status=resolution_status, limit=limit
        )
    )
    return EntityResolutionsResponse(items=items, count=len(items))


@router.get("/api/v1/analyses", response_model=AssessmentsResponse)
async def analyses(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
) -> AssessmentsResponse:
    items = tuple(request.app.state.analyses.list_latest(limit=limit))
    return AssessmentsResponse(items=items, count=len(items))


@router.get("/api/v1/strategies", response_model=StrategyRegistryResponse)
async def strategies(request: Request) -> StrategyRegistryResponse:
    items = request.app.state.strategy_registry.manifests()
    return StrategyRegistryResponse(items=items, count=len(items))


@router.get("/api/v1/setups", response_model=StrategyEvaluationsResponse)
async def strategy_setups(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> StrategyEvaluationsResponse:
    items = tuple(request.app.state.strategy_evaluations.list_latest(limit=limit))
    return StrategyEvaluationsResponse(items=items, count=len(items))


@router.get("/api/v1/alerts", response_model=AlertsResponse)
async def alerts(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> AlertsResponse:
    items = tuple(
        AlertView(
            evaluation=evaluation,
            assessment=request.app.state.analyses.get(evaluation.assessment_id),
        )
        for evaluation in request.app.state.strategy_evaluations.list_latest(limit=limit)
    )
    return AlertsResponse(items=items, count=len(items))


@router.get(
    "/api/v1/setups/{symbol}/history",
    response_model=StrategyEvaluationsResponse,
)
async def strategy_setup_history(
    request: Request,
    symbol: str,
    strategy_id: str | None = Query(default=None, min_length=1, max_length=80),
    limit: int = Query(default=200, ge=1, le=500),
) -> StrategyEvaluationsResponse:
    items = tuple(
        request.app.state.strategy_evaluations.history(
            _ticker(symbol), strategy_id=strategy_id, limit=limit
        )
    )
    return StrategyEvaluationsResponse(items=items, count=len(items))


@router.get("/api/v1/backtests", response_model=BacktestRunsResponse)
async def backtest_runs(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> BacktestRunsResponse:
    items = tuple(request.app.state.backtests.list_run_summaries(limit=limit))
    return BacktestRunsResponse(items=items, count=len(items))


@router.post("/api/v1/backtests/run", response_model=BacktestExecution)
def run_backtest(
    request: Request, payload: BacktestRequest
) -> BacktestExecution:
    try:
        return request.app.state.backtest_service.execute(payload)
    except (BacktestResearchError, ValueError) as exc:
        code = (
            status.HTTP_409_CONFLICT
            if "already running" in str(exc)
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get(
    "/api/v1/backtests/experiments",
    response_model=BacktestExperimentsResponse,
)
async def backtest_experiments(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> BacktestExperimentsResponse:
    items = tuple(request.app.state.backtests.list_experiments(limit=limit))
    return BacktestExperimentsResponse(items=items, count=len(items))


@router.post(
    "/api/v1/backtests/experiments/run",
    response_model=ExperimentExecution,
)
def run_backtest_experiment(
    request: Request, payload: WalkForwardExperimentRequest
) -> ExperimentExecution:
    try:
        return request.app.state.backtest_service.execute_experiment(payload)
    except (BacktestResearchError, ValueError) as exc:
        code = (
            status.HTTP_409_CONFLICT
            if "already running" in str(exc)
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get(
    "/api/v1/backtests/experiments/{experiment_hash}",
    response_model=StoredBacktestExperiment,
)
async def backtest_experiment(
    request: Request, experiment_hash: str
) -> StoredBacktestExperiment:
    stored = request.app.state.backtests.get_experiment(_sha256(experiment_hash))
    if stored is None:
        raise HTTPException(status_code=404, detail="backtest experiment not found")
    return stored


@router.get("/api/v1/backtests/{run_hash}", response_model=StoredBacktestRun)
async def backtest_run(request: Request, run_hash: str) -> StoredBacktestRun:
    stored = request.app.state.backtests.get_run(_sha256(run_hash))
    if stored is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    return stored


@router.get("/api/v1/analyses/{symbol}", response_model=AIAssessment)
async def latest_analysis(request: Request, symbol: str) -> AIAssessment:
    normalized = _ticker(symbol)
    assessment = request.app.state.analyses.latest(normalized)
    if assessment is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return assessment


@router.get(
    "/api/v1/analyses/{symbol}/history", response_model=AssessmentsResponse
)
async def analysis_history(
    request: Request,
    symbol: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> AssessmentsResponse:
    items = tuple(request.app.state.analyses.history(_ticker(symbol), limit=limit))
    return AssessmentsResponse(items=items, count=len(items))


@router.get(
    "/api/v1/analyses/{symbol}/evidence", response_model=AnalysisEvidencePackage
)
async def analysis_evidence(
    request: Request, symbol: str
) -> AnalysisEvidencePackage:
    assessment = request.app.state.analyses.latest(_ticker(symbol))
    if assessment is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    package = request.app.state.analyses.get_package(assessment.evidence_hash)
    if package is None:
        raise HTTPException(status_code=404, detail="analysis evidence not found")
    return package


@router.get(
    "/api/v1/analysis-outcomes/{assessment_id}",
    response_model=AnalysisOutcomesResponse,
)
async def analysis_outcomes(
    request: Request, assessment_id: str
) -> AnalysisOutcomesResponse:
    items = tuple(request.app.state.analyses.outcomes(assessment_id))
    return AnalysisOutcomesResponse(items=items, count=len(items))


def _ticker(symbol: str) -> str:
    normalized = symbol.upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", normalized):
        raise HTTPException(status_code=422, detail="invalid ticker symbol")
    return normalized


def _manual_action(value: str) -> ManualAction:
    try:
        return ManualAction(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown manual operation") from exc


async def _require_form_csrf_token(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=403, detail="dashboard control token missing")
    body = await request.body()
    if len(body) > 4_096:
        raise HTTPException(status_code=403, detail="dashboard control token invalid")
    try:
        values = parse_qs(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=4,
            strict_parsing=True,
        ).get("csrf_token", [])
    except (UnicodeDecodeError, ValueError):
        values = []
    provided = values[0] if len(values) == 1 else None
    _require_csrf_token(request, provided)


def _require_csrf_token(request: Request, provided: str | None) -> None:
    expected = request.app.state.csrf_token
    if provided is None or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=403,
            detail="dashboard control token missing or expired; reload the dashboard",
        )


def _sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise HTTPException(status_code=422, detail="invalid research identifier")
    return value
