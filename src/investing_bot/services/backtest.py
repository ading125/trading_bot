"""Stored-data backtest execution and walk-forward research acceptance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict

from investing_bot.backtesting import BacktestEngine
from investing_bot.db import (
    BacktestRepository,
    JobRunRepository,
    MarketDataRepository,
    StoredBacktestExperiment,
    StoredBacktestRun,
)
from investing_bot.models import (
    BacktestRequest,
    ParameterTrialReport,
    ResearchAcceptanceReport,
    WalkForwardExperimentReport,
    WalkForwardExperimentRequest,
)
from investing_bot.strategies import StrategyRegistry


class BacktestResearchError(ValueError):
    pass


class BacktestExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run: StoredBacktestRun
    cached: bool


class ExperimentExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment: StoredBacktestExperiment
    cached: bool


class BacktestResearchService:
    """Run deterministic experiments without involving candidates or an LLM."""

    def __init__(
        self,
        *,
        market: MarketDataRepository,
        strategies: StrategyRegistry,
        repository: BacktestRepository,
        jobs: JobRunRepository,
        engine: BacktestEngine | None = None,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.market = market
        self.strategies = strategies
        self.repository = repository
        self.jobs = jobs
        self.engine = engine or BacktestEngine()
        self._now = now

    def execute(self, request: BacktestRequest) -> BacktestExecution:
        request_hash = _hash(request.model_dump(mode="json"))
        owner = f"backtest-{request.strategy_id}-{request_hash[:12]}"
        job_type = f"backtest:{request.strategy_id}"
        if not self.jobs.acquire_lease(
            job_type=job_type,
            owner=owner,
            lease_duration=timedelta(minutes=30),
        ):
            raise BacktestResearchError("a backtest for this strategy is already running")
        job = self.jobs.create(
            job_type=job_type,
            code_version="0.1.0",
            config_hash=request_hash,
        )
        self.jobs.start(job.run_id, owner=owner)
        try:
            strategy = self.strategies.get(request.strategy_id)
            warmup_start = request.period.start - timedelta(days=730)
            bars_by_symbol = {
                symbol: self.market.research_bars(
                    provider_id=request.provider_id,
                    symbol=symbol,
                    start=warmup_start,
                    end=request.period.end,
                )
                for symbol in request.symbols
            }
            benchmark = self.market.research_bars(
                provider_id=request.provider_id,
                symbol=request.benchmark_symbol,
                start=warmup_start,
                end=request.period.end,
            )
            missing = [symbol for symbol, bars in bars_by_symbol.items() if not bars]
            if missing or not benchmark:
                names = ", ".join((*missing, *(() if benchmark else (request.benchmark_symbol,))))
                raise BacktestResearchError(
                    f"adjusted daily research history is missing for: {names}"
                )
            result = self.engine.run(
                request,
                strategy=strategy,
                bars_by_symbol=bars_by_symbol,
                benchmark_bars=benchmark,
            )
            cached = self.repository.get_run(result.run_hash)
            if cached is not None:
                self.jobs.succeed(job.run_id, finished_at=self._now())
                return BacktestExecution(run=cached, cached=True)
            stored = StoredBacktestRun(
                request=request,
                result=result,
                created_at=self._now(),
            )
            self.repository.store_run(stored)
            self.jobs.succeed(job.run_id, finished_at=self._now())
            return BacktestExecution(run=stored, cached=False)
        except Exception as exc:
            self.jobs.fail(job.run_id, error_summary=str(exc) or type(exc).__name__)
            raise
        finally:
            self.jobs.release_lease(job_type=job_type, owner=owner)

    def execute_experiment(
        self, request: WalkForwardExperimentRequest
    ) -> ExperimentExecution:
        request_hash = _hash(request.model_dump(mode="json"))
        owner = f"experiment-{request.strategy_id}-{request_hash[:12]}"
        job_type = f"backtest_experiment:{request.strategy_id}"
        if not self.jobs.acquire_lease(
            job_type=job_type,
            owner=owner,
            lease_duration=timedelta(hours=2),
        ):
            raise BacktestResearchError(
                "a walk-forward experiment for this strategy is already running"
            )
        job = self.jobs.create(
            job_type=job_type,
            code_version="0.1.0",
            config_hash=request_hash,
        )
        self.jobs.start(job.run_id, owner=owner)
        try:
            trials: list[ParameterTrialReport] = []
            for candidate in request.candidates:
                executions = []
                for period in (
                    request.plan.development,
                    request.plan.validation,
                ):
                    executions.append(
                        self.execute(
                            BacktestRequest(
                                strategy_id=request.strategy_id,
                                strategy_parameters=candidate.parameters,
                                symbols=request.symbols,
                                benchmark_symbol=request.benchmark_symbol,
                                provider_id=request.provider_id,
                                period=period,
                                starting_capital=request.starting_capital,
                                allocation_per_trade=request.allocation_per_trade,
                                costs=request.costs,
                                current_constituents_only=(
                                    request.current_constituents_only
                                ),
                            )
                        )
                    )
                development, validation = (
                    execution.run.result for execution in executions
                )
                trials.append(
                    ParameterTrialReport(
                        candidate=candidate,
                        parameters_hash=development.parameters_hash,
                        development_run_hash=development.run_hash,
                        validation_run_hash=validation.run_hash,
                        development_metrics=development.metrics,
                        validation_metrics=validation.metrics,
                    )
                )
            selected = max(
                trials,
                key=lambda trial: (
                    trial.development_metrics.expectancy_pct
                    if trial.development_metrics.expectancy_pct is not None
                    else float("-inf"),
                    trial.development_metrics.total_return_pct,
                ),
            )
            out_of_sample = self.execute(
                BacktestRequest(
                    strategy_id=request.strategy_id,
                    strategy_parameters=selected.candidate.parameters,
                    symbols=request.symbols,
                    benchmark_symbol=request.benchmark_symbol,
                    provider_id=request.provider_id,
                    period=request.plan.out_of_sample,
                    starting_capital=request.starting_capital,
                    allocation_per_trade=request.allocation_per_trade,
                    costs=request.costs,
                    current_constituents_only=request.current_constituents_only,
                )
            ).run.result
            selected_index = trials.index(selected)
            selected = ParameterTrialReport.model_validate(
                {
                    **selected.model_dump(),
                    "out_of_sample_run_hash": out_of_sample.run_hash,
                    "out_of_sample_metrics": out_of_sample.metrics.model_dump(),
                }
            )
            trials[selected_index] = selected
            nearby_trials = [
                trial
                for trial in trials
                if _parameters_are_nearby(
                    selected.candidate.parameters, trial.candidate.parameters
                )
            ]
            nearby_count = len(nearby_trials)
            nearby_positive = sum(
                _positive(trial.validation_metrics) for trial in nearby_trials
            )
            parameter_stability_met = nearby_count >= 3 and nearby_positive >= 2
            validation_positive = _positive(selected.validation_metrics)
            out_of_sample_positive = _positive(out_of_sample.metrics)
            minimum_trade_count_met = (
                selected.validation_metrics.trade_count
                >= request.minimum_trades_per_holdout
                and out_of_sample.metrics.trade_count
                >= request.minimum_trades_per_holdout
            )
            drawdown_limit_met = (
                selected.validation_metrics.maximum_drawdown_pct
                >= -request.maximum_acceptable_drawdown_pct
                and out_of_sample.metrics.maximum_drawdown_pct
                >= -request.maximum_acceptable_drawdown_pct
            )
            checks = (
                parameter_stability_met,
                validation_positive,
                out_of_sample_positive,
                minimum_trade_count_met,
                drawdown_limit_met,
            )
            reasons = []
            if not parameter_stability_met:
                reasons.append("nearby_parameter_stability_not_demonstrated")
            if not validation_positive:
                reasons.append("validation_expectancy_not_positive")
            if not out_of_sample_positive:
                reasons.append("out_of_sample_expectancy_not_positive")
            if not minimum_trade_count_met:
                reasons.append("holdout_trade_count_below_minimum")
            if not drawdown_limit_met:
                reasons.append("maximum_drawdown_limit_exceeded")
            acceptance = ResearchAcceptanceReport(
                passed=all(checks),
                selected_candidate_id=selected.candidate.candidate_id,
                nearby_parameter_sets_tested=nearby_count,
                nearby_parameter_sets_positive=nearby_positive,
                parameter_stability_met=parameter_stability_met,
                validation_positive=validation_positive,
                out_of_sample_positive=out_of_sample_positive,
                minimum_trade_count_met=minimum_trade_count_met,
                drawdown_limit_met=drawdown_limit_met,
                reasons=tuple(reasons),
                live_alerts_may_be_enabled=all(checks),
            )
            experiment_hash = _hash(
                {
                    "request": request.model_dump(mode="json"),
                    "trials": [
                        {
                            "candidate_id": trial.candidate.candidate_id,
                            "development": trial.development_run_hash,
                            "validation": trial.validation_run_hash,
                            "out_of_sample": trial.out_of_sample_run_hash,
                        }
                        for trial in trials
                    ],
                }
            )
            prior = self.repository.get_experiment(experiment_hash)
            if prior is not None:
                self.jobs.succeed(job.run_id, finished_at=self._now())
                return ExperimentExecution(experiment=prior, cached=True)
            strategy = self.strategies.get(request.strategy_id)
            report = WalkForwardExperimentReport(
                experiment_hash=experiment_hash,
                strategy_id=request.strategy_id,
                strategy_version=strategy.manifest.version,
                provider_id=request.provider_id,
                plan=request.plan,
                trials=tuple(trials),
                acceptance=acceptance,
                ai_outcomes_included=False,
                disclosures=(
                    "Parameter selection uses development results; nearby variants are checked in validation, then the final out-of-sample period runs once for the selected candidate.",
                    "Historical AI decisions are not reconstructed or included in these performance claims.",
                    "Passing this mechanical gate does not automatically change a strategy manifest or enable alerts.",
                    (
                        "Current-constituent universe results are survivorship-biased."
                        if request.current_constituents_only
                        else "Point-in-time universe membership remains a required data-quality check."
                    ),
                ),
            )
            stored = StoredBacktestExperiment(
                request=request,
                report=report,
                created_at=self._now(),
            )
            self.repository.store_experiment(stored)
            self.jobs.succeed(job.run_id, finished_at=self._now())
            return ExperimentExecution(experiment=stored, cached=False)
        except Exception as exc:
            self.jobs.fail(job.run_id, error_summary=str(exc) or type(exc).__name__)
            raise
        finally:
            self.jobs.release_lease(job_type=job_type, owner=owner)


def _positive(metrics) -> bool:
    return bool(
        metrics.expectancy_pct is not None
        and metrics.expectancy_pct > 0
        and metrics.total_return_pct > 0
    )


def _parameters_are_nearby(
    selected: dict[str, object], candidate: dict[str, object]
) -> bool:
    shared = set(selected) & set(candidate)
    numeric_comparisons = 0
    for key in shared:
        left = selected[key]
        right = candidate[key]
        if isinstance(left, bool) or isinstance(right, bool):
            if left != right:
                return False
            continue
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            numeric_comparisons += 1
            scale = max(abs(float(left)), 1.0)
            if abs(float(right) - float(left)) / scale > 0.25:
                return False
        elif left != right:
            return False
    return numeric_comparisons > 0 or selected == candidate


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
