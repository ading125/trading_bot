"""Point-in-time strategy evaluation over stored canonical market data."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging

from pydantic import BaseModel, ConfigDict, Field

from investing_bot.db import (
    AnalysisRepository,
    CandidateRepository,
    JobRunRepository,
    MarketDataRepository,
    StoredStrategyEvaluation,
    StrategyRepository,
)
from investing_bot.models import (
    AnalysisDecision,
    BarInterval,
    MarketFrame,
    SetupState,
    StrategyExplanation,
)
from investing_bot.strategies import StrategyRegistry


logger = logging.getLogger(__name__)


class StrategyEvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    eligible_for_strategy: bool
    reason: str
    evaluations: tuple[StoredStrategyEvaluation, ...] = ()
    cached_evaluations: int = Field(default=0, ge=0)


class StrategyEvaluationService:
    """Evaluate packaged hypotheses without network, LLM, or filesystem access."""

    def __init__(
        self,
        *,
        market: MarketDataRepository,
        candidates: CandidateRepository,
        analyses: AnalysisRepository,
        strategies: StrategyRegistry,
        repository: StrategyRepository,
        jobs: JobRunRepository,
        benchmark_symbol: str = "SPY",
        daily_bar_limit: int = 400,
        intraday_bar_limit: int = 100,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.market = market
        self.candidates = candidates
        self.analyses = analyses
        self.strategies = strategies
        self.repository = repository
        self.jobs = jobs
        self.benchmark_symbol = benchmark_symbol
        self.daily_bar_limit = daily_bar_limit
        self.intraday_bar_limit = intraday_bar_limit
        self._now = now

    def evaluate_symbol(
        self,
        symbol: str,
        *,
        as_of: datetime | None = None,
        parameter_overrides: Mapping[str, Mapping[str, object]] | None = None,
    ) -> StrategyEvaluationSummary:
        ticker = symbol.upper()
        evaluated_at = as_of or self._now()
        owner = f"strategy-{ticker}-{sha256(evaluated_at.isoformat().encode()).hexdigest()[:12]}"
        job_type = f"strategy_evaluation:{ticker}"
        if not self.jobs.acquire_lease(
            job_type=job_type,
            owner=owner,
            lease_duration=timedelta(minutes=5),
        ):
            return StrategyEvaluationSummary(
                symbol=ticker,
                eligible_for_strategy=False,
                reason="evaluation_already_running",
            )
        run = self.jobs.create(
            job_type=job_type,
            code_version="0.1.0",
            config_hash=_registry_hash(self.strategies),
        )
        self.jobs.start(run.run_id, owner=owner)
        try:
            summary = self._evaluate(
                ticker,
                as_of=evaluated_at,
                parameter_overrides=parameter_overrides or {},
            )
            self.jobs.succeed(run.run_id, finished_at=self._now())
            return summary
        except Exception as exc:
            self.jobs.fail(run.run_id, error_summary=str(exc) or type(exc).__name__)
            raise
        finally:
            self.jobs.release_lease(job_type=job_type, owner=owner)

    def _evaluate(
        self,
        ticker: str,
        *,
        as_of: datetime,
        parameter_overrides: Mapping[str, Mapping[str, object]],
    ) -> StrategyEvaluationSummary:
        candidate = self.candidates.get_candidate(ticker)
        if candidate is None:
            return _skipped(ticker, "active_candidate_required")
        assessment = self.analyses.latest(ticker)
        if assessment is None or assessment.created_at > as_of:
            return _skipped(ticker, "current_ai_assessment_required")
        if assessment.decision is not AnalysisDecision.QUALIFY:
            return _skipped(ticker, "ai_qualification_required")
        provider_id = self.market.resolve_strategy_provider(
            symbol=ticker,
            benchmark_symbol=self.benchmark_symbol,
            as_of=as_of,
        )
        if provider_id is None:
            return _skipped(ticker, "provider_consistent_daily_data_required")
        daily = self.market.strategy_bars(
            provider_id=provider_id,
            symbol=ticker,
            interval=BarInterval.DAY_1,
            as_of=as_of,
            limit=self.daily_bar_limit,
        )
        benchmark = (
            daily
            if ticker == self.benchmark_symbol
            else self.market.strategy_bars(
                provider_id=provider_id,
                symbol=self.benchmark_symbol,
                interval=BarInterval.DAY_1,
                as_of=as_of,
                limit=self.daily_bar_limit,
            )
        )
        intraday = self.market.strategy_bars(
            provider_id=provider_id,
            symbol=ticker,
            interval=BarInterval.MINUTE_15,
            as_of=as_of,
            limit=self.intraday_bar_limit,
        )
        input_hash = _frame_hash(
            symbol=ticker,
            benchmark_symbol=self.benchmark_symbol,
            provider_id=provider_id,
            daily=daily,
            benchmark=benchmark,
            intraday=intraday,
        )
        frame = MarketFrame(
            symbol=ticker,
            benchmark_symbol=self.benchmark_symbol,
            provider_id=provider_id,
            as_of=as_of,
            input_hash=input_hash,
            daily_bars=daily,
            benchmark_daily_bars=benchmark,
            intraday_bars=intraday,
        )
        completed: list[StoredStrategyEvaluation] = []
        cached_count = 0
        for strategy in self.strategies.strategies():
            manifest = strategy.manifest
            parameters = strategy.validate_parameters(
                parameter_overrides.get(manifest.strategy_id)
            )
            parameter_values = parameters.model_dump(mode="json")
            parameters_hash = _hash(parameter_values)
            signal = strategy.evaluate(frame, parameters)
            signal = self._preserve_state_when_stale(signal, ticker)
            cache_key = _hash(
                {
                    "assessment_id": assessment.assessment_id,
                    "strategy_id": manifest.strategy_id,
                    "strategy_version": manifest.version,
                    "parameters_hash": parameters_hash,
                    "input_hash": input_hash,
                    "stale": signal.stale,
                    "state": signal.state.value,
                }
            )
            cached = self.repository.find_cached(cache_key)
            if cached is not None:
                completed.append(cached)
                cached_count += 1
                continue
            evaluation = StoredStrategyEvaluation(
                evaluation_id=_hash({"evaluation": cache_key}),
                cache_key=cache_key,
                assessment_id=assessment.assessment_id,
                parameters=parameter_values,
                parameters_hash=parameters_hash,
                input_hash=input_hash,
                provider_id=provider_id,
                as_of=as_of,
                research_status=manifest.research_status,
                signal=signal,
                created_at=self._now(),
            )
            self.repository.store(evaluation)
            completed.append(evaluation)
        return StrategyEvaluationSummary(
            symbol=ticker,
            eligible_for_strategy=True,
            reason="evaluated",
            evaluations=tuple(completed),
            cached_evaluations=cached_count,
        )

    def _preserve_state_when_stale(self, signal, ticker: str):
        if not signal.stale:
            return signal
        previous = self.repository.latest(ticker, signal.strategy_id)
        if previous is None or previous.signal.state not in {
            SetupState.FORMING,
            SetupState.CONFIRMED,
        }:
            return signal
        explanation = StrategyExplanation(
            summary=(
                f"Market data is stale; preserved the prior "
                f"{previous.signal.state.value} state and blocked new confirmation."
            ),
            reason_codes=tuple(
                dict.fromkeys((*signal.explanation.reason_codes, "stale_data"))
            ),
            conditions=signal.explanation.conditions,
        )
        return previous.signal.model_copy(
            update={
                "stale": True,
                "confirmation_blocked": True,
                "features": signal.features,
                "explanation": explanation,
            }
        )


class StrategyPollingService:
    def __init__(
        self,
        service: StrategyEvaluationService,
        *,
        symbols: tuple[str, ...],
        interval_seconds: int,
        initial_delay_seconds: float = 20,
    ) -> None:
        self.service = service
        self.symbols = symbols
        self.interval_seconds = interval_seconds
        self.initial_delay_seconds = initial_delay_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="strategy-evaluation-polling"
            )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(self.initial_delay_seconds)
        while True:
            for symbol in self.symbols:
                try:
                    summary = self.service.evaluate_symbol(symbol)
                    logger.info(
                        "strategy evaluation completed",
                        extra={
                            "ticker": symbol,
                            "eligible": summary.eligible_for_strategy,
                            "reason": summary.reason,
                            "evaluations": len(summary.evaluations),
                            "cached": summary.cached_evaluations,
                        },
                    )
                except Exception:
                    logger.exception(
                        "strategy evaluation failed", extra={"ticker": symbol}
                    )
            await asyncio.sleep(self.interval_seconds)


def _skipped(symbol: str, reason: str) -> StrategyEvaluationSummary:
    return StrategyEvaluationSummary(
        symbol=symbol,
        eligible_for_strategy=False,
        reason=reason,
    )


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _registry_hash(registry: StrategyRegistry) -> str:
    return _hash([manifest.model_dump(mode="json") for manifest in registry.manifests()])


def _frame_hash(
    *,
    symbol: str,
    benchmark_symbol: str,
    provider_id: str,
    daily,
    benchmark,
    intraday,
) -> str:
    return _hash(
        {
            "symbol": symbol,
            "benchmark_symbol": benchmark_symbol,
            "provider_id": provider_id,
            "daily": [bar.model_dump(mode="json") for bar in daily],
            "benchmark": [bar.model_dump(mode="json") for bar in benchmark],
            "intraday": [bar.model_dump(mode="json") for bar in intraday],
        }
    )
