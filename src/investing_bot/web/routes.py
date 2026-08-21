"""Read-only foundation routes for health and the dashboard shell."""

from __future__ import annotations

from importlib.metadata import version
from datetime import date
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from investing_bot.models import (
    BacktestRequest,
    BarInterval,
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
)


APP_VERSION = version("investing-bot")
_WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))

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
        request.app.state.analyses.list_latest(limit=10)
        if hasattr(request.app.state, "analyses")
        else []
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
            "analysis_count": (
                request.app.state.analyses.count()
                if hasattr(request.app.state, "analyses")
                else 0
            ),
            "strategy_manifests": strategy_manifests,
            "strategy_evaluations": strategy_evaluations,
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
        },
    )


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


def _sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise HTTPException(status_code=422, detail="invalid research identifier")
    return value
