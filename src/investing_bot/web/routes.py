"""Read-only foundation routes for health and the dashboard shell."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict


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
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "ready": ready,
            "migration_version": database.latest_migration if database else None,
            "version": APP_VERSION,
        },
    )
