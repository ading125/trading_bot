from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from investing_bot.db import (
    BacktestRepository,
    Database,
    JobRunRepository,
    MarketDataRepository,
)
from investing_bot.models import (
    BacktestPeriod,
    BacktestRequest,
    ParameterCandidate,
    ResearchPeriodName,
    WalkForwardExperimentRequest,
    WalkForwardPlan,
)
from investing_bot.services import BacktestResearchService
from investing_bot.strategies import build_default_strategy_registry
from tests.test_strategy_service import BAR_START, NOW, _market_bar


def _service(tmp_path: Path):
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    assert database.migrate() == 8
    market = MarketDataRepository(database, dataset_root=tmp_path / "market")
    bars = []
    for index in range(62):
        if index == 60:
            candidate_close = 132.0
            candidate_volume = 2_000_000
        elif index == 61:
            candidate_close = 132.6
            candidate_volume = 1_200_000
        else:
            candidate_close = 100 + index * 0.5
            candidate_volume = 1_000_000
        bars.extend(
            (
                _market_bar(
                    "CVX",
                    index=index,
                    close=candidate_close,
                    volume=candidate_volume,
                ),
                _market_bar(
                    "SPY",
                    index=index,
                    close=100 + index * 0.1,
                    volume=1_000_000,
                ),
            )
        )
    market.store_bars(tuple(bars))
    repository = BacktestRepository(database)
    service = BacktestResearchService(
        market=market,
        strategies=build_default_strategy_registry(),
        repository=repository,
        jobs=JobRunRepository(database),
        now=lambda: NOW,
    )
    return database, repository, service


def _period(name: ResearchPeriodName, start_index: int, end_index: int) -> BacktestPeriod:
    return BacktestPeriod(
        name=name,
        start=(BAR_START + timedelta(days=start_index)).date(),
        end=(BAR_START + timedelta(days=end_index)).date(),
    )


def test_backtest_service_persists_and_reuses_deterministic_run(tmp_path: Path) -> None:
    database, repository, service = _service(tmp_path)
    request = BacktestRequest(
        strategy_id="breakout",
        symbols=("CVX",),
        benchmark_symbol="SPY",
        provider_id="fixture_recorded",
        period=_period(ResearchPeriodName.DEVELOPMENT, 50, 61),
    )

    first = service.execute(request)
    second = service.execute(request)

    assert first.cached is False
    assert second.cached is True
    assert second.run.result == first.run.result
    assert repository.run_count() == 1
    assert repository.get_run(first.run.result.run_hash) == first.run
    summary = repository.list_run_summaries()[0]
    assert summary.run_hash == first.run.result.run_hash
    assert summary.trade_count == first.run.result.metrics.trade_count
    assert first.run.result.report.ai_outcomes_included is False
    assert first.run.result.ending_cash == first.run.result.ending_equity
    database.close()


def test_walk_forward_report_preserves_trials_and_keeps_failed_gate_disabled(
    tmp_path: Path,
) -> None:
    database, repository, service = _service(tmp_path)
    request = WalkForwardExperimentRequest(
        strategy_id="breakout",
        symbols=("CVX",),
        benchmark_symbol="SPY",
        provider_id="fixture_recorded",
        plan=WalkForwardPlan(
            development=_period(ResearchPeriodName.DEVELOPMENT, 50, 53),
            validation=_period(ResearchPeriodName.VALIDATION, 54, 57),
            out_of_sample=_period(ResearchPeriodName.OUT_OF_SAMPLE, 58, 61),
        ),
        candidates=(
            ParameterCandidate(
                candidate_id="volume_110",
                parameters={"minimum_volume_ratio": 1.1},
            ),
            ParameterCandidate(
                candidate_id="volume_120",
                parameters={"minimum_volume_ratio": 1.2},
            ),
            ParameterCandidate(
                candidate_id="volume_130",
                parameters={"minimum_volume_ratio": 1.3},
            ),
        ),
        minimum_trades_per_holdout=1,
    )

    first = service.execute_experiment(request)
    second = service.execute_experiment(request)

    report = first.experiment.report
    assert first.cached is False
    assert second.cached is True
    assert len(report.trials) == 3
    assert report.acceptance.nearby_parameter_sets_tested == 3
    assert sum(trial.out_of_sample_metrics is not None for trial in report.trials) == 1
    assert report.acceptance.passed is False
    assert report.acceptance.live_alerts_may_be_enabled is False
    assert "nearby_parameter_stability_not_demonstrated" in report.acceptance.reasons
    assert "holdout_trade_count_below_minimum" in report.acceptance.reasons
    assert report.ai_outcomes_included is False
    assert repository.experiment_count() == 1
    assert repository.get_experiment(report.experiment_hash) == first.experiment
    database.close()
