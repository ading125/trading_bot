from __future__ import annotations

from pathlib import Path
from hashlib import sha256

from httpx2 import ASGITransport, AsyncClient
import pytest

from investing_bot.app import create_app
from investing_bot.config import AppSettings
from investing_bot.db import Database, JobRunRepository, JobStatus
from investing_bot.models import ProviderCapability
from investing_bot.providers import (
    CapabilitySelection,
    ProviderConfiguration,
    ProviderTarget,
)


@pytest.mark.anyio
async def test_health_readiness_and_dashboard(tmp_path: Path) -> None:
    app = create_app(
        AppSettings(
            environment="test",
            data_dir=tmp_path,
            log_format="console",
        )
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            health = await client.get("/api/v1/health")
            ready = await client.get("/api/v1/ready")
            providers = await client.get("/api/v1/providers")
            provider_health = await client.get("/api/v1/providers/health")
            credential_status = await client.get("/api/v1/credentials/status")
            source_health = await client.get("/api/v1/sources/health")
            posts = await client.get("/api/v1/sources/civictracker/posts")
            market_status = await client.get("/api/v1/market/status")
            market_bars = await client.get(
                "/api/v1/market/bars?symbol=SPY&interval=1d&adjustment=adjusted"
            )
            market_datasets = await client.get("/api/v1/market/datasets")
            candidates = await client.get("/api/v1/candidates")
            resolutions = await client.get("/api/v1/resolutions")
            evidence = await client.get("/api/v1/candidates/CVX/evidence")
            analyses = await client.get("/api/v1/analyses")
            missing_analysis = await client.get("/api/v1/analyses/CVX")
            dashboard = await client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["migration_version"] == 5
    assert providers.status_code == 200
    assert len(providers.json()["providers"]) == 5
    assert providers.json()["selections"]["daily_bars"]["primary"]["provider_id"] == (
        "fixture_recorded"
    )
    assert "credential_ref" not in providers.text
    assert provider_health.status_code == 200
    assert len(provider_health.json()["results"]) == 18
    assert all(
        item["state"] == "healthy" for item in provider_health.json()["results"]
    )
    assert credential_status.json() == {
        "initialized": False,
        "unlocked": False,
        "configured_references": 0,
    }
    assert source_health.status_code == 200
    assert len(source_health.json()["results"]) == 18
    assert posts.status_code == 200
    assert posts.json() == {"items": [], "count": 0}
    assert market_status.json()["bar_count"] == 0
    assert market_status.json()["quarantine_count"] == 0
    assert market_bars.json() == {"items": [], "count": 0}
    assert market_datasets.json() == {"items": [], "count": 0}
    assert candidates.json() == {"items": [], "count": 0}
    assert resolutions.json() == {"items": [], "count": 0}
    assert evidence.json() == {"items": [], "count": 0}
    assert analyses.json() == {"items": [], "count": 0}
    assert missing_analysis.status_code == 404
    assert dashboard.status_code == 200
    assert "The local research service is running." in dashboard.text
    assert "No recommendation is generated" in dashboard.text
    assert "Configured providers" in dashboard.text
    assert "Market bars" in dashboard.text
    assert "Current verified companies" in dashboard.text
    assert "Latest AI assessments" in dashboard.text

    for response in (health, ready, dashboard):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"].startswith(
            "default-src 'self'"
        )


@pytest.mark.anyio
async def test_api_responses_disable_caching(tmp_path: Path) -> None:
    app = create_app(
        AppSettings(environment="test", data_dir=tmp_path, log_format="console")
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/health")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


@pytest.mark.anyio
async def test_readiness_fails_before_lifespan_initializes_database(
    tmp_path: Path,
) -> None:
    app = create_app(
        AppSettings(environment="test", data_dir=tmp_path, log_format="console")
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["migration_version"] is None


@pytest.mark.anyio
async def test_shutdown_marks_running_jobs_interrupted(tmp_path: Path) -> None:
    settings = AppSettings(
        environment="test", data_dir=tmp_path, log_format="console"
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        repository = app.state.job_runs
        run = repository.create(
            job_type="shutdown_check",
            code_version="0.1.0",
            config_hash=sha256(b"shutdown-config").hexdigest(),
        )
        repository.start(run.run_id, owner="test-worker")

    database = Database(settings.database_path)
    database.connect()
    database.migrate()
    persisted = JobRunRepository(database).get(run.run_id)
    database.close()

    assert persisted.status is JobStatus.INTERRUPTED
    assert persisted.error_summary == "application stopped before job completed"


@pytest.mark.anyio
async def test_local_provider_configuration_changes_startup_selection(
    tmp_path: Path,
) -> None:
    configuration = ProviderConfiguration(
        selections={
            ProviderCapability.NEWS: CapabilitySelection(
                primary=ProviderTarget(provider_id="fixture_backup")
            )
        }
    )
    (tmp_path / "providers.json").write_text(
        configuration.model_dump_json(indent=2), encoding="utf-8"
    )
    app = create_app(
        AppSettings(environment="test", data_dir=tmp_path, log_format="console")
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/providers")

    assert response.status_code == 200
    assert response.json()["configuration_hash"] == configuration.configuration_hash
    assert response.json()["selections"]["news"]["primary"]["provider_id"] == (
        "fixture_backup"
    )
