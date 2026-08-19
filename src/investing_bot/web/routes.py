"""Read-only foundation routes for health and the dashboard shell."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from investing_bot.models import ProviderCapability
from investing_bot.providers.contracts import (
    ConnectionTestResult,
    ProviderManifest,
)
from investing_bot.db import StoredSocialPost


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
    credential_configured: bool


class CapabilitySelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: ProviderTargetResponse
    fallbacks: tuple[ProviderTargetResponse, ...]


class ProviderHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: tuple[ConnectionTestResult, ...]


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
    healthy_providers = len(
        {item.provider_id for item in provider_health if item.available}
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "ready": ready,
            "migration_version": database.latest_migration if database else None,
            "version": APP_VERSION,
            "provider_health": provider_health,
            "healthy_providers": healthy_providers,
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
            targets.append(
                ProviderTargetResponse(
                    provider_id=target.provider_id,
                    credential_required=manifest.authentication_required,
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
