from __future__ import annotations

from pathlib import Path
from hashlib import sha256

from httpx2 import ASGITransport, AsyncClient
import pytest

from investing_bot.app import create_app
from investing_bot.config import AppSettings
from investing_bot.db import Database, JobRunRepository, JobStatus


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
            dashboard = await client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["migration_version"] == 1
    assert dashboard.status_code == 200
    assert "The local research service is running." in dashboard.text
    assert "No recommendation is generated" in dashboard.text

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
