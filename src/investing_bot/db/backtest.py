"""Immutable backtest runs and walk-forward experiment history."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict

from investing_bot.db.database import Database
from investing_bot.models import (
    BacktestPeriod,
    BacktestRequest,
    BacktestResult,
    ResearchPeriodName,
    WalkForwardExperimentReport,
    WalkForwardExperimentRequest,
)


class StoredBacktestRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request: BacktestRequest
    result: BacktestResult
    created_at: AwareDatetime


class StoredBacktestExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request: WalkForwardExperimentRequest
    report: WalkForwardExperimentReport
    created_at: AwareDatetime


class BacktestRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_hash: str
    strategy_id: str
    strategy_version: str
    provider_id: str
    period: BacktestPeriod
    total_return_pct: float
    maximum_drawdown_pct: float
    sharpe_ratio: float | None
    trade_count: int
    benchmark_return_pct: float
    created_at: AwareDatetime


class BacktestRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def store_run(self, run: StoredBacktestRun) -> None:
        result = run.result
        self._database.execute(
            """
            INSERT INTO backtest_runs VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            ) ON CONFLICT (run_hash) DO NOTHING
            """,
            [
                result.run_hash,
                result.strategy_id,
                result.strategy_version,
                result.provider_id,
                result.period.name.value,
                result.period.start,
                result.period.end,
                result.parameters_hash,
                result.data_hash,
                run.request.model_dump_json(),
                result.model_dump_json(),
                result.metrics.total_return_pct,
                result.metrics.maximum_drawdown_pct,
                result.metrics.sharpe_ratio,
                result.metrics.trade_count,
                result.benchmark.total_return_pct,
                run.created_at,
            ],
        )

    def get_run(self, run_hash: str) -> StoredBacktestRun | None:
        row = self._database.fetchone(
            "SELECT request_json, result_json, created_at FROM backtest_runs WHERE run_hash=?",
            [run_hash],
        )
        return None if row is None else _run_from_row(row)

    def list_runs(self, *, limit: int = 100) -> list[StoredBacktestRun]:
        rows = self._database.fetchall(
            """
            SELECT request_json, result_json, created_at FROM backtest_runs
            ORDER BY created_at DESC, run_hash LIMIT ?
            """,
            [limit],
        )
        return [_run_from_row(row) for row in rows]

    def list_run_summaries(self, *, limit: int = 100) -> list[BacktestRunSummary]:
        rows = self._database.fetchall(
            """
            SELECT run_hash, strategy_id, strategy_version, provider_id,
                   period_name, period_start, period_end, total_return_pct,
                   maximum_drawdown_pct, sharpe_ratio, trade_count,
                   benchmark_return_pct, created_at
            FROM backtest_runs ORDER BY created_at DESC, run_hash LIMIT ?
            """,
            [limit],
        )
        return [
            BacktestRunSummary(
                run_hash=row[0],
                strategy_id=row[1],
                strategy_version=row[2],
                provider_id=row[3],
                period=BacktestPeriod(
                    name=ResearchPeriodName(row[4]), start=row[5], end=row[6]
                ),
                total_return_pct=row[7],
                maximum_drawdown_pct=row[8],
                sharpe_ratio=row[9],
                trade_count=row[10],
                benchmark_return_pct=row[11],
                created_at=row[12],
            )
            for row in rows
        ]

    def run_count(self) -> int:
        row = self._database.fetchone("SELECT COUNT(*) FROM backtest_runs")
        return 0 if row is None else int(row[0])

    def store_experiment(self, experiment: StoredBacktestExperiment) -> None:
        report = experiment.report
        self._database.execute(
            """
            INSERT INTO backtest_experiments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (experiment_hash) DO NOTHING
            """,
            [
                report.experiment_hash,
                report.strategy_id,
                report.strategy_version,
                report.provider_id,
                report.acceptance.selected_candidate_id,
                report.acceptance.passed,
                experiment.request.model_dump_json(),
                report.model_dump_json(),
                experiment.created_at,
            ],
        )

    def get_experiment(
        self, experiment_hash: str
    ) -> StoredBacktestExperiment | None:
        row = self._database.fetchone(
            """
            SELECT request_json, report_json, created_at
            FROM backtest_experiments WHERE experiment_hash=?
            """,
            [experiment_hash],
        )
        return None if row is None else _experiment_from_row(row)

    def list_experiments(
        self, *, limit: int = 100
    ) -> list[StoredBacktestExperiment]:
        rows = self._database.fetchall(
            """
            SELECT request_json, report_json, created_at
            FROM backtest_experiments
            ORDER BY created_at DESC, experiment_hash LIMIT ?
            """,
            [limit],
        )
        return [_experiment_from_row(row) for row in rows]

    def experiment_count(self) -> int:
        row = self._database.fetchone("SELECT COUNT(*) FROM backtest_experiments")
        return 0 if row is None else int(row[0])


def _run_from_row(row: tuple[object, ...]) -> StoredBacktestRun:
    return StoredBacktestRun(
        request=BacktestRequest.model_validate_json(row[0]),
        result=BacktestResult.model_validate_json(row[1]),
        created_at=row[2],
    )


def _experiment_from_row(row: tuple[object, ...]) -> StoredBacktestExperiment:
    return StoredBacktestExperiment(
        request=WalkForwardExperimentRequest.model_validate_json(row[0]),
        report=WalkForwardExperimentReport.model_validate_json(row[1]),
        created_at=row[2],
    )
