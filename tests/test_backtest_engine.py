from __future__ import annotations

from collections.abc import Mapping
import ast
from datetime import UTC, datetime, timedelta
from typing import Any
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from investing_bot.backtesting import BacktestEngine, BacktestInvariantError
from investing_bot.models import (
    BacktestCostModel,
    BacktestPeriod,
    BacktestRequest,
    BarInterval,
    EntryIntent,
    ExitIntent,
    MarketFrame,
    OrderReason,
    ResearchPeriodName,
    SetupState,
    StopIntent,
    StrategyBar,
    StrategyCondition,
    StrategyExplanation,
    StrategyFeatures,
    StrategyManifest,
    StrategyResearchStatus,
    StrategySignal,
    WalkForwardPlan,
)


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


class ScriptedParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    confirmation_bar: int = 2
    trailing_atr_multiple: float = 100
    maximum_holding_sessions: int = 10


class ScriptedStrategy:
    @property
    def manifest(self) -> StrategyManifest:
        defaults = ScriptedParameters()
        return StrategyManifest(
            strategy_id="scripted_test",
            display_name="Scripted test",
            version="1.0.0",
            description="Deterministic test-only strategy.",
            research_status=StrategyResearchStatus.HYPOTHESIS,
            live_alerts_enabled=False,
            required_intervals=(BarInterval.DAY_1,),
            warmup_daily_bars=2,
            parameter_schema=ScriptedParameters.model_json_schema(),
            default_parameters=defaults.model_dump(mode="json"),
        )

    def validate_parameters(
        self, values: Mapping[str, Any] | BaseModel | None = None
    ) -> ScriptedParameters:
        if values is None:
            return ScriptedParameters()
        if isinstance(values, ScriptedParameters):
            return values
        if isinstance(values, BaseModel):
            values = values.model_dump()
        return ScriptedParameters.model_validate(values)

    def evaluate(
        self, frame: MarketFrame, parameters: BaseModel
    ) -> StrategySignal:
        assert isinstance(parameters, ScriptedParameters)
        current = frame.daily_bars[-1]
        features = _features(frame)
        if len(frame.daily_bars) == parameters.confirmation_bar:
            return StrategySignal(
                symbol=frame.symbol,
                strategy_id=self.manifest.strategy_id,
                strategy_version=self.manifest.version,
                state=SetupState.CONFIRMED,
                stale=False,
                confirmation_blocked=False,
                features=features,
                explanation=_explanation("scripted confirmation"),
                entry=EntryIntent(
                    trigger_price=102,
                    zone_low=102,
                    zone_high=103,
                    valid_after=current.bar_end,
                ),
                stop=StopIntent(
                    invalidation_price=95,
                    rationale="Scripted protective boundary.",
                ),
                exit=ExitIntent(
                    profit_target=110,
                    trailing_atr_multiple=parameters.trailing_atr_multiple,
                    maximum_holding_sessions=parameters.maximum_holding_sessions,
                ),
                reward_to_risk=1.14,
            )
        return StrategySignal(
            symbol=frame.symbol,
            strategy_id=self.manifest.strategy_id,
            strategy_version=self.manifest.version,
            state=SetupState.INVALIDATED,
            stale=False,
            confirmation_blocked=False,
            features=features,
            explanation=_explanation("no scripted confirmation"),
        )


class FutureLeakingStrategy(ScriptedStrategy):
    def evaluate(
        self, frame: MarketFrame, parameters: BaseModel
    ) -> StrategySignal:
        signal = super().evaluate(frame, parameters)
        return signal.model_copy(
            update={
                "features": signal.features.model_copy(
                    update={"data_through": frame.as_of + timedelta(days=1)}
                )
            }
        )


def _features(frame: MarketFrame) -> StrategyFeatures:
    current = frame.daily_bars[-1]
    return StrategyFeatures(
        daily_bar_count=len(frame.daily_bars),
        benchmark_bar_count=len(frame.benchmark_daily_bars),
        intraday_bar_count=0,
        data_through=current.bar_end,
        close=current.close,
        fast_moving_average=current.close,
        slow_moving_average=current.close,
        atr=1,
        average_volume=1_000_000,
        relative_strength_pct=1,
        distance_to_fast_atr=0,
        breakout_level=current.high,
        volume_ratio=1,
    )


def _explanation(summary: str) -> StrategyExplanation:
    return StrategyExplanation(
        summary=summary,
        reason_codes=(),
        conditions=(
            StrategyCondition(
                condition_id="scripted",
                passed=True,
                observed="completed bars",
                requirement="test fixture",
            ),
        ),
    )


def _bars(symbol: str) -> tuple[StrategyBar, ...]:
    values = (
        (100, 101, 99, 100),
        (100, 102, 99.5, 101),
        (101.5, 103, 101, 102),
        (102, 111, 94, 104),
        (104, 105, 103, 104.5),
    )
    bars = []
    for index, (open_, high, low, close) in enumerate(values):
        start = START + timedelta(days=index)
        end = start + timedelta(hours=6, minutes=30)
        bars.append(
            StrategyBar(
                symbol=symbol,
                interval=BarInterval.DAY_1,
                bar_start=start,
                bar_end=end,
                session_date=start.date(),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1_000_000,
                known_available_at=end,
                provider_id="fixture_recorded",
            )
        )
    return tuple(bars)


def _request(**parameters: Any) -> BacktestRequest:
    return BacktestRequest(
        strategy_id="scripted_test",
        strategy_parameters=parameters,
        symbols=("CVX",),
        benchmark_symbol="SPY",
        provider_id="fixture_recorded",
        period=BacktestPeriod(
            name=ResearchPeriodName.DEVELOPMENT,
            start=START.date(),
            end=(START + timedelta(days=4)).date(),
        ),
        starting_capital=100_000,
        allocation_per_trade=0.5,
        costs=BacktestCostModel(commission_bps=1, slippage_bps=5),
    )


def test_event_engine_is_deterministic_reconciled_and_never_fills_same_close() -> None:
    engine = BacktestEngine()
    strategy = ScriptedStrategy()
    candidate = _bars("CVX")
    benchmark = _bars("SPY")

    first = engine.run(
        _request(),
        strategy=strategy,
        bars_by_symbol={"CVX": candidate},
        benchmark_bars=benchmark,
    )
    second = engine.run(
        _request(),
        strategy=strategy,
        bars_by_symbol={"CVX": candidate},
        benchmark_bars=benchmark,
    )

    assert first == second
    assert len(first.trades) == 1
    trade = first.trades[0]
    assert trade.entered_at > trade.entry_signal_at
    assert trade.exit_reason is OrderReason.PROTECTIVE_STOP
    assert first.ending_cash == pytest.approx(
        first.starting_capital + trade.net_pnl
    )
    assert first.total_costs == pytest.approx(
        sum(fill.commission + fill.slippage_cost for fill in first.fills)
    )
    assert all(
        fill.filled_at >= next(
            order.eligible_after for order in first.orders if order.order_id == fill.order_id
        )
        for fill in first.fills
    )
    assert first.metrics.trade_count == 1
    assert first.benchmark.symbol == "SPY"
    assert first.report.ai_outcomes_included is False
    assert first.report.survivorship_bias_warning is True


def test_time_exit_is_queued_at_close_and_filled_at_next_open() -> None:
    gentle = list(_bars("CVX"))
    gentle[3] = gentle[3].model_copy(
        update={"open": 104, "high": 106, "low": 103, "close": 105}
    )
    result = BacktestEngine().run(
        _request(maximum_holding_sessions=1),
        strategy=ScriptedStrategy(),
        bars_by_symbol={"CVX": tuple(gentle)},
        benchmark_bars=_bars("SPY"),
    )

    trade = result.trades[0]
    assert trade.exit_reason is OrderReason.TIME_EXIT
    exit_order = next(order for order in result.orders if order.order_id == trade.exit_order_id)
    assert exit_order.signal_bar_end is not None
    assert trade.exited_at > exit_order.signal_bar_end


def test_engine_rejects_future_dated_strategy_output() -> None:
    with pytest.raises(BacktestInvariantError, match="future feature data"):
        BacktestEngine().run(
            _request(),
            strategy=FutureLeakingStrategy(),
            bars_by_symbol={"CVX": _bars("CVX")},
            benchmark_bars=_bars("SPY"),
        )


def test_profit_target_and_trailing_stop_paths_are_accounted() -> None:
    target_bars = list(_bars("CVX"))
    target_bars[3] = target_bars[3].model_copy(
        update={"open": 111, "high": 112, "low": 109, "close": 111}
    )
    target = BacktestEngine().run(
        _request(),
        strategy=ScriptedStrategy(),
        bars_by_symbol={"CVX": tuple(target_bars)},
        benchmark_bars=_bars("SPY"),
    )
    assert target.trades[0].exit_reason is OrderReason.PROFIT_TARGET

    trailing_bars = list(_bars("CVX"))
    trailing_bars[3] = trailing_bars[3].model_copy(
        update={"open": 102, "high": 104, "low": 100, "close": 101}
    )
    trailing = BacktestEngine().run(
        _request(trailing_atr_multiple=2),
        strategy=ScriptedStrategy(),
        bars_by_symbol={"CVX": tuple(trailing_bars)},
        benchmark_bars=_bars("SPY"),
    )
    assert trailing.trades[0].exit_reason is OrderReason.TRAILING_STOP


def test_walk_forward_periods_cannot_overlap() -> None:
    with pytest.raises(ValueError, match="cannot overlap"):
        WalkForwardPlan(
            development=BacktestPeriod(
                name=ResearchPeriodName.DEVELOPMENT,
                start=START.date(),
                end=(START + timedelta(days=10)).date(),
            ),
            validation=BacktestPeriod(
                name=ResearchPeriodName.VALIDATION,
                start=(START + timedelta(days=10)).date(),
                end=(START + timedelta(days=20)).date(),
            ),
            out_of_sample=BacktestPeriod(
                name=ResearchPeriodName.OUT_OF_SAMPLE,
                start=(START + timedelta(days=21)).date(),
                end=(START + timedelta(days=30)).date(),
            ),
        )


def test_backtest_engine_package_has_no_io_or_provider_imports() -> None:
    root = Path(__file__).parents[1] / "src" / "investing_bot" / "backtesting"
    prohibited = {
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "pathlib",
        "yfinance",
        "investing_bot.db",
        "investing_bot.providers",
        "investing_bot.services",
    }
    violations: list[str] = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if any(
                    module == item or module.startswith(f"{item}.")
                    for item in prohibited
                ):
                    violations.append(f"{path.name}:{node.lineno}:{module}")
    assert violations == []
