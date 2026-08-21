"""Deliberately sanitized local diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict

from investing_bot.config import AppSettings
from investing_bot.db import Database, JobRunRepository
from investing_bot.providers import ProviderManager


class DiagnosticProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    capability: str
    state: str
    checked_at: AwareDatetime
    latency_ms: float
    error_code: str | None


class DiagnosticJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_type: str
    status: str
    requested_at: AwareDatetime
    finished_at: AwareDatetime | None


class SanitizedDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: AwareDatetime
    environment: str
    bind_host: str
    port: int
    migration_version: int
    vault_initialized: bool
    vault_unlocked: bool
    configured_credential_count: int
    collection_enabled: dict[str, bool]
    record_counts: dict[str, int]
    providers: tuple[DiagnosticProvider, ...]
    recent_jobs: tuple[DiagnosticJob, ...]


def build_sanitized_diagnostics(
    *,
    settings: AppSettings,
    database: Database,
    jobs: JobRunRepository,
    provider_manager: ProviderManager,
    vault_status: object,
) -> SanitizedDiagnostics:
    counts: dict[str, int] = {}
    for label, table in (
        ("source_posts", "social_posts"),
        ("market_bars", "market_bars"),
        ("candidates", "candidates"),
        ("assessments", "ai_assessments"),
        ("strategy_evaluations", "strategy_evaluations"),
        ("backtests", "backtest_runs"),
    ):
        row = database.fetchone(f"SELECT COUNT(*) FROM {table}")
        counts[label] = 0 if row is None else int(row[0])
    providers = tuple(
        DiagnosticProvider(
            provider_id=item.provider_id,
            capability=item.capability.value,
            state=item.state.value,
            checked_at=item.checked_at,
            latency_ms=item.latency_ms,
            error_code=None if item.error_code is None else item.error_code.value,
        )
        for item in provider_manager.health_snapshot()
    )
    recent_jobs = tuple(
        DiagnosticJob(
            job_type=item.job_type,
            status=item.status.value,
            requested_at=item.requested_at,
            finished_at=item.finished_at,
        )
        for item in jobs.list_recent(limit=25)
    )
    references = tuple(getattr(vault_status, "references", ()))
    return SanitizedDiagnostics(
        generated_at=datetime.now(UTC),
        environment=settings.environment,
        bind_host=str(settings.bind_host),
        port=settings.port,
        migration_version=database.latest_migration,
        vault_initialized=bool(getattr(vault_status, "initialized", False)),
        vault_unlocked=bool(getattr(vault_status, "unlocked", False)),
        configured_credential_count=len(references),
        collection_enabled={
            "civictracker": settings.civictracker_collection_enabled,
            "market": settings.market_collection_enabled,
            "candidates": settings.candidate_refresh_enabled,
            "analysis": settings.analysis_refresh_enabled,
            "strategies": settings.strategy_refresh_enabled,
        },
        record_counts=counts,
        providers=providers,
        recent_jobs=recent_jobs,
    )
