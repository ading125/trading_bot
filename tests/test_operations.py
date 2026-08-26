from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from investing_bot.backup import (
    BackupError,
    create_encrypted_backup,
    restore_encrypted_backup,
)
from investing_bot.db import (
    AIAssessment,
    AnalysisRepository,
    Database,
    JobRunRepository,
    MarketDataRepository,
    OperationsRepository,
)
from investing_bot.models import (
    AnalysisDecision,
    BarInterval,
    CanonicalMetadata,
    MarketBar,
    ManualAction,
    OperationalTask,
    PolicyRelevance,
    PriceAdjustment,
)
from investing_bot.services import (
    OperationalScheduler,
    OperationsCoordinator,
    OperationsSchedulePlanner,
    OutcomeTrackingService,
    USMarketCalendar,
)


def test_market_calendar_handles_holidays_early_close_and_schedule() -> None:
    calendar = USMarketCalendar()
    planner = OperationsSchedulePlanner(calendar)

    assert not calendar.is_session(date(2026, 4, 3))  # Good Friday
    assert not calendar.is_session(date(2026, 8, 22))  # Saturday
    assert calendar.session_close(date(2026, 11, 27)).time() == time(13)
    next_runs = planner.next_runs(datetime(2026, 8, 21, 20, 20, tzinfo=UTC))

    assert len(next_runs) == len(OperationalTask)
    daily = next(item for item in next_runs if item.task is OperationalTask.DAILY_PRICES)
    assert daily.market_session_date == date(2026, 8, 24)
    assert daily.scheduled_for.time() == time(16, 10)


@pytest.mark.anyio
async def test_full_market_day_schedule_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    database.migrate()
    repository = OperationsRepository(database)
    planner = OperationsSchedulePlanner()
    dispatched: list[OperationalTask] = []

    async def handler(task: OperationalTask) -> None:
        dispatched.append(task)

    scheduler = OperationalScheduler(
        planner=planner,
        repository=repository,
        handler=handler,
    )
    start = datetime(2026, 8, 21, 6, 59, tzinfo=UTC)
    end = datetime(2026, 8, 21, 21, 0, tzinfo=UTC)

    first_count = await scheduler.run_window(start, end)
    second_count = await scheduler.run_window(start, end)

    expected = {
        OperationalTask.CIVICTRACKER,
        OperationalTask.BROAD_NEWS,
        OperationalTask.ACTIVE_NEWS,
        OperationalTask.EARNINGS,
        OperationalTask.DAILY_PRICES,
        OperationalTask.INTRADAY_PRICES,
        OperationalTask.AFTER_CLOSE_REPORT,
        OperationalTask.PROSPECTIVE_OUTCOMES,
    }
    assert expected.issubset(set(dispatched))
    assert first_count == len(dispatched)
    assert second_count == 0
    assert all(item.status == "succeeded" for item in repository.list_recent())
    database.close()


@pytest.mark.anyio
async def test_analysis_shortlist_continues_after_one_company_fails(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    database.migrate()

    class CandidateQueue:
        @staticmethod
        def analysis_queue(*, limit: int) -> tuple[str, ...]:
            assert limit == 5
            return ("MSFT", "AAPL", "NVDA")

    class PartialAnalysis:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def analyze(self, symbol: str) -> SimpleNamespace:
            self.calls.append(symbol)
            if symbol == "AAPL":
                raise RuntimeError("provider rejected structured output")
            return SimpleNamespace(cached=symbol == "MSFT")

    analysis = PartialAnalysis()
    coordinator = OperationsCoordinator(
        jobs=JobRunRepository(database),
        config_hash="a" * 64,
        source_collector=None,
        market_poller=None,
        candidate_service=CandidateQueue(),
        analysis_service=analysis,
        strategy_service=None,
        outcome_service=None,
        analysis_symbols=(),
        analysis_candidate_limit=5,
        strategy_symbols=(),
    )

    result = await coordinator.run_manual(ManualAction.ANALYSIS)

    assert analysis.calls == ["MSFT", "AAPL", "NVDA"]
    assert result.summary == (
        "analysis completed for 2 automatically selected symbols (1 cached); "
        "1 failed (AAPL)"
    )
    assert JobRunRepository(database).latest_for_type(
        "manual_refresh:analysis"
    ).status == "succeeded"
    database.close()


def test_prospective_outcomes_use_only_later_known_daily_sessions(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    database.migrate()
    analyses = AnalysisRepository(database)
    market = MarketDataRepository(database, dataset_root=tmp_path / "market")
    created_at = datetime(2026, 8, 3, 12, tzinfo=UTC)
    assessment_id = "assessment-outcome-test"
    analyses.store_assessment(
        AIAssessment(
            assessment_id=assessment_id,
            cache_key="cache-outcome-test",
            evidence_hash="e" * 64,
            ticker="CVX",
            company_name="Chevron Corporation",
            decision=AnalysisDecision.QUALIFY,
            growth_score=85,
            evidence_quality=90,
            policy_relevance=PolicyRelevance.NONE,
            catalysts=("Capacity expansion",),
            earnings_assessment="Evidence supports monitoring.",
            bullish_thesis="A bounded prospective thesis.",
            bearish_case="Execution risk remains.",
            risks=("Commodity prices",),
            uncertainties=("Timing",),
            source_ids=("source-1",),
            prompt_version="growth.v1",
            output_schema_version="analysis.v1",
            provider_id="fixture_recorded",
            model_id="fixture-analysis",
            adapter_version="1.0.0",
            provider_request_id="request-outcome-test",
            configuration_hash="c" * 64,
            created_at=created_at,
        )
    )
    calendar = USMarketCalendar()
    session = created_at.date()
    bars: list[MarketBar] = []
    for index in range(21):
        while not calendar.is_session(session):
            session += timedelta(days=1)
        bars.append(_daily_bar(session, 100.0 + index))
        session += timedelta(days=1)
    market.store_bars(tuple(bars))
    service = OutcomeTrackingService(analyses=analyses, market=market)

    summary = service.reconcile(as_of=bars[-1].bar_end + timedelta(minutes=1))
    outcomes = analyses.outcomes(assessment_id)

    assert summary.baselines_recorded == 3
    assert summary.outcomes_recorded == 3
    assert summary.still_pending == 0
    assert [item.return_pct for item in outcomes] == pytest.approx([5.0, 10.0, 20.0])
    assert all(item.market_provider_id == "fixture_recorded" for item in outcomes)
    database.close()


def test_encrypted_backup_round_trip_and_tamper_rejection(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "investing_bot.duckdb").write_bytes(b"database-state")
    credentials = data / "credentials"
    credentials.mkdir()
    (credentials / "cred_groq.json").write_text(
        '{"ciphertext":"not-plaintext-provider-secret"}', encoding="utf-8"
    )
    backup = tmp_path / "state.ibbackup"
    passphrase = "correct horse battery staple"

    create_encrypted_backup(data, backup, passphrase)
    encrypted = backup.read_bytes()
    assert b"database-state" not in encrypted
    restored = tmp_path / "restored"
    restore_encrypted_backup(backup, restored, passphrase)
    assert (restored / "investing_bot.duckdb").read_bytes() == b"database-state"
    assert (restored / "credentials" / "cred_groq.json").is_file()

    tampered = bytearray(encrypted)
    tampered[len(tampered) // 2] ^= 1
    damaged = tmp_path / "damaged.ibbackup"
    damaged.write_bytes(tampered)
    with pytest.raises(BackupError, match="modified"):
        restore_encrypted_backup(damaged, tmp_path / "damaged-restore", passphrase)


def _daily_bar(session: date, close: float) -> MarketBar:
    bar_start = datetime.combine(session, time(13, 30), UTC)
    bar_end = datetime.combine(session, time(20), UTC)
    identity = f"CVX:{session}:{close}"
    return MarketBar(
        symbol="CVX",
        interval=BarInterval.DAY_1,
        bar_start=bar_start,
        bar_end=bar_end,
        session_date=session,
        exchange_timezone="America/New_York",
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1_000_000,
        adjustment=PriceAdjustment.ADJUSTED,
        metadata=CanonicalMetadata(
            provider_id="fixture_recorded",
            provider_record_id=identity,
            event_at=bar_end,
            known_available_at=bar_end,
            retrieved_at=bar_end,
            raw_payload_hash=sha256(identity.encode()).hexdigest(),
            schema_version="market_bar.v1",
            adapter_version="1.0.0",
            dataset_lineage="fixture_recorded:adjusted:daily",
        ),
    )
