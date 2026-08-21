from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from httpx2 import ASGITransport, AsyncClient
import pytest

from investing_bot.app import create_app
from investing_bot.config import AppSettings
from investing_bot.db import (
    CandidateEvidence,
    CandidateSourceType,
    Database,
    JobRunRepository,
    JobStatus,
)
from investing_bot.models import ProviderCapability
from investing_bot.providers import (
    CapabilitySelection,
    CredentialPresenceStore,
    ProviderConfiguration,
    ProviderManager,
    ProviderTarget,
    build_default_registry,
    default_provider_configuration,
)
from investing_bot.providers.contracts import (
    ConnectionTestResult,
    ProviderHealthState,
)
from investing_bot.web.routes import _analysis_mode_view


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
            csrf_headers = {"X-CSRF-Token": app.state.csrf_token}
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
            strategies = await client.get("/api/v1/strategies")
            setups = await client.get("/api/v1/setups")
            alerts = await client.get("/api/v1/alerts")
            setup_history = await client.get("/api/v1/setups/CVX/history")
            provider_operations = await client.get("/api/v1/providers/operations")
            operations_schedule = await client.get("/api/v1/operations/schedule")
            operation_runs = await client.get("/api/v1/operations/runs")
            diagnostics = await client.get("/api/v1/diagnostics")
            outcome_refresh = await client.post(
                "/api/v1/operations/refresh/outcomes", headers=csrf_headers
            )
            repeated_refresh = await client.post(
                "/api/v1/operations/refresh/outcomes", headers=csrf_headers
            )
            cross_site_refresh = await client.post(
                "/api/v1/operations/refresh/outcomes",
                headers={"Origin": "https://attacker.example"},
            )
            equivalent_loopback_refresh = await client.post(
                "http://127.0.0.1/api/v1/operations/refresh/outcomes",
                headers={
                    **csrf_headers,
                    "Origin": "http://localhost",
                    "Sec-Fetch-Site": "cross-site",
                },
            )
            dashboard_form_refresh = await client.post(
                "/actions/refresh/outcomes",
                data={"csrf_token": app.state.csrf_token},
                headers={
                    "Origin": "https://embedded-browser.invalid",
                    "Sec-Fetch-Site": "cross-site",
                },
            )
            missing_form_token = await client.post("/actions/refresh/outcomes")
            backtests = await client.get("/api/v1/backtests")
            experiments = await client.get("/api/v1/backtests/experiments")
            missing_backtest = await client.get(f"/api/v1/backtests/{'a' * 64}")
            missing_experiment = await client.get(
                f"/api/v1/backtests/experiments/{'b' * 64}"
            )
            dashboard = await client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["migration_version"] == 9
    assert providers.status_code == 200
    assert len(providers.json()["providers"]) == 6
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
    assert strategies.status_code == 200
    assert strategies.json()["count"] == 2
    assert {item["strategy_id"] for item in strategies.json()["items"]} == {
        "breakout",
        "trend_pullback",
    }
    assert all(
        item["research_status"] == "hypothesis"
        and item["live_alerts_enabled"] is False
        for item in strategies.json()["items"]
    )
    assert setups.json() == {"items": [], "count": 0}
    assert alerts.json() == {"items": [], "count": 0}
    assert setup_history.json() == {"items": [], "count": 0}
    assert provider_operations.status_code == 200
    assert len(provider_operations.json()["items"]) == 18
    assert all(
        item["contract_status"] == "compatible"
        for item in provider_operations.json()["items"]
    )
    assert operations_schedule.status_code == 200
    assert {item["task"] for item in operations_schedule.json()["items"]} == {
        "civictracker",
        "broad_news",
        "active_news",
        "earnings",
        "daily_prices",
        "intraday_prices",
        "after_close_report",
        "prospective_outcomes",
    }
    assert operation_runs.json() == {"items": []}
    assert diagnostics.status_code == 200
    assert diagnostics.json()["migration_version"] == 9
    assert str(tmp_path) not in diagnostics.text
    assert "cred_groq" not in diagnostics.text
    assert outcome_refresh.status_code == 200
    assert outcome_refresh.json()["action"] == "outcomes"
    assert repeated_refresh.status_code == 429
    assert cross_site_refresh.status_code == 403
    assert equivalent_loopback_refresh.status_code == 429
    assert dashboard_form_refresh.status_code == 303
    assert missing_form_token.status_code == 403
    assert backtests.json() == {"items": [], "count": 0}
    assert experiments.json() == {"items": [], "count": 0}
    assert missing_backtest.status_code == 404
    assert missing_experiment.status_code == 404
    assert dashboard.status_code == 200
    assert app.state.csrf_token in dashboard.text
    assert dashboard.headers["cache-control"] == "no-store"
    assert "The local research service is running." in dashboard.text
    assert "Market-day controls" in dashboard.text
    assert "No recommendation is generated" in dashboard.text
    assert "Capability contracts" in dashboard.text
    assert "Market bars" in dashboard.text
    assert "Current verified companies" in dashboard.text
    assert "Latest AI assessments" in dashboard.text
    assert "Technical strategy hypotheses" in dashboard.text
    assert "These baselines are implementation hypotheses" in dashboard.text
    assert "Walk-forward backtest reports" in dashboard.text
    assert "They do not reconstruct historical" in dashboard.text
    assert "No deterministic backtest has been run yet" in dashboard.text
    assert "OFFLINE ANALYSIS — RECORDED FALLBACK" in dashboard.text
    assert "No analysis evidence is sent to a hosted AI." in dashboard.text
    assert "fixture_recorded · recorded-analysis-v1" in dashboard.text

    for response in (health, ready, dashboard):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"].startswith(
            "default-src 'self'"
        )


@pytest.mark.anyio
async def test_manual_analysis_uses_automatic_candidate_queue(tmp_path: Path) -> None:
    app = create_app(
        AppSettings(
            environment="test",
            data_dir=tmp_path,
            log_format="console",
            analysis_candidate_limit=1,
        )
    )
    now = datetime.now(UTC)

    async with app.router.lifespan_context(app):
        for symbol, company_name, minutes_ago in (
            ("CVX", "Chevron Corporation", 10),
            ("AAPL", "Apple Inc.", 1),
        ):
            observed_at = now - timedelta(minutes=minutes_ago)
            app.state.candidates.store_evidence(
                CandidateEvidence(
                    evidence_id=f"news-{symbol.casefold()}-automatic",
                    symbol=symbol,
                    company_name=company_name,
                    source_type=CandidateSourceType.NEWS,
                    source_record_id=f"news-{symbol.casefold()}-automatic",
                    source_excerpt=f"Recent material update for {company_name}.",
                    source_url=f"https://example.test/{symbol.casefold()}",
                    event_at=observed_at,
                    observed_at=observed_at,
                    extraction_method="source_symbol",
                    resolution_id=None,
                    resolution_confidence=1,
                    relevance=0.9,
                    expires_at=now + timedelta(days=30),
                    active=True,
                )
            )
        app.state.candidates.refresh_candidate_state(now=now)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/actions/refresh/analysis",
                data={"csrf_token": app.state.csrf_token},
            )
            analyses = await client.get("/api/v1/analyses")

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/?operation=analysis&state=completed#analysis-heading"
    )
    assert analyses.json()["count"] == 1
    assert analyses.json()["items"][0]["ticker"] == "AAPL"


def test_analysis_mode_reports_live_hosted_provider() -> None:
    credentials = CredentialPresenceStore(references=("cred_groq",))
    manager = ProviderManager(
        registry=build_default_registry(credentials=credentials),
        configuration=default_provider_configuration(live_analysis=True),
        credentials=credentials,
    )
    health = ConnectionTestResult(
        provider_id="groq",
        capability=ProviderCapability.STRUCTURED_LLM,
        state=ProviderHealthState.HEALTHY,
        checked_at=datetime(2026, 8, 20, 18, 0, tzinfo=UTC),
        latency_ms=25,
    )

    mode = _analysis_mode_view(manager, (health,))

    assert mode == {
        "state": "live",
        "title": "LIVE AI ACTIVE",
        "detail": (
            "New assessments use the hosted model with bounded, "
            "source-attributed evidence."
        ),
        "provider": "groq · openai/gpt-oss-120b",
    }


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
