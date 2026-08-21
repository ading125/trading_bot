from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from investing_bot.models import BarInterval, MarketFrame, SetupState, StrategyBar
from investing_bot.strategies import build_default_strategy_registry
from investing_bot.strategies.breakout import BreakoutStrategy


START = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)


def _daily_bars(
    symbol: str,
    closes: list[float],
    *,
    final_volume: int = 2_000_000,
) -> tuple[StrategyBar, ...]:
    bars = []
    for index, close in enumerate(closes):
        bar_start = START + timedelta(days=index)
        bar_end = bar_start + timedelta(hours=6, minutes=30)
        bars.append(
            StrategyBar(
                symbol=symbol,
                interval=BarInterval.DAY_1,
                bar_start=bar_start,
                bar_end=bar_end,
                session_date=bar_start.date(),
                open=close - 0.2,
                high=close + 0.4,
                low=close - 0.6,
                close=close,
                volume=(final_volume if index == len(closes) - 1 else 1_000_000),
                known_available_at=bar_end,
                provider_id="fixture_recorded",
            )
        )
    return tuple(bars)


def _breakout_frame(*, stale_days: int = 0, intraday: bool = False) -> MarketFrame:
    candidate = _daily_bars(
        "CVX",
        [100 + index * 0.5 for index in range(60)] + [132.0],
    )
    benchmark = _daily_bars(
        "SPY",
        [100 + index * 0.1 for index in range(61)],
        final_volume=1_000_000,
    )
    intraday_bars: tuple[StrategyBar, ...] = ()
    if intraday:
        day = candidate[-1].bar_start
        first_end = day + timedelta(hours=4)
        second_end = first_end + timedelta(minutes=15)
        intraday_bars = (
            StrategyBar(
                symbol="CVX",
                interval=BarInterval.MINUTE_15,
                bar_start=first_end - timedelta(minutes=15),
                bar_end=first_end,
                session_date=day.date(),
                open=131.0,
                high=131.4,
                low=130.8,
                close=131.2,
                volume=100_000,
                known_available_at=first_end,
                provider_id="fixture_recorded",
            ),
            StrategyBar(
                symbol="CVX",
                interval=BarInterval.MINUTE_15,
                bar_start=first_end,
                bar_end=second_end,
                session_date=day.date(),
                open=131.2,
                high=132.0,
                low=131.1,
                close=131.8,
                volume=150_000,
                known_available_at=second_end,
                provider_id="fixture_recorded",
            ),
        )
    return MarketFrame(
        symbol="CVX",
        benchmark_symbol="SPY",
        provider_id="fixture_recorded",
        as_of=candidate[-1].bar_end + timedelta(hours=1, days=stale_days),
        input_hash="a" * 64,
        daily_bars=candidate,
        benchmark_daily_bars=benchmark,
        intraday_bars=intraday_bars,
    )


def test_default_registry_exposes_unvalidated_research_hypotheses() -> None:
    registry = build_default_strategy_registry()
    manifests = registry.manifests()

    assert [manifest.strategy_id for manifest in manifests] == [
        "breakout",
        "trend_pullback",
    ]
    assert all(manifest.research_status.value == "hypothesis" for manifest in manifests)
    assert all(manifest.live_alerts_enabled is False for manifest in manifests)
    assert all(manifest.parameter_schema for manifest in manifests)


def test_breakout_is_deterministic_and_uses_completed_bars() -> None:
    strategy = BreakoutStrategy()
    parameters = strategy.validate_parameters()
    frame = _breakout_frame()

    first = strategy.evaluate(frame, parameters)
    second = strategy.evaluate(frame, parameters)

    assert first == second
    assert first.state is SetupState.CONFIRMED
    assert first.stale is False
    assert first.confirmation_blocked is False
    assert first.entry is not None and first.stop is not None and first.exit is not None
    assert first.entry.valid_after == frame.daily_bars[-1].bar_end
    assert first.stop.invalidation_price < first.entry.zone_low
    assert first.reward_to_risk == 2.0


def test_optional_intraday_confirmation_only_uses_completed_intraday_bars() -> None:
    strategy = BreakoutStrategy()
    parameters = strategy.validate_parameters({"require_intraday_confirmation": True})

    without_intraday = strategy.evaluate(_breakout_frame(), parameters)
    with_intraday = strategy.evaluate(_breakout_frame(intraday=True), parameters)

    assert without_intraday.state is SetupState.FORMING
    assert with_intraday.state is SetupState.CONFIRMED
    assert with_intraday.features.intraday_confirmed is True


def test_stale_data_blocks_new_confirmation() -> None:
    strategy = BreakoutStrategy()
    signal = strategy.evaluate(
        _breakout_frame(stale_days=10), strategy.validate_parameters()
    )

    assert signal.state is SetupState.FORMING
    assert signal.stale is True
    assert signal.confirmation_blocked is True
    assert "stale_data" in signal.explanation.reason_codes


def test_market_frame_rejects_future_information_and_provider_blending() -> None:
    frame = _breakout_frame()
    final = frame.daily_bars[-1]
    future = final.model_copy(
        update={
            "bar_start": frame.as_of + timedelta(minutes=1),
            "bar_end": frame.as_of + timedelta(hours=1),
            "known_available_at": frame.as_of + timedelta(hours=1),
            "session_date": (frame.as_of + timedelta(days=1)).date(),
        }
    )
    with pytest.raises(ValidationError, match="future information"):
        frame.model_copy(update={"daily_bars": (*frame.daily_bars, future)}).model_validate(
            frame.model_copy(update={"daily_bars": (*frame.daily_bars, future)}).model_dump()
        )

    blended = final.model_copy(update={"provider_id": "different_provider"})
    with pytest.raises(ValidationError, match="cannot blend providers"):
        MarketFrame(
            **frame.model_dump(exclude={"daily_bars"}),
            daily_bars=(*frame.daily_bars[:-1], blended),
        )


def test_invalid_strategy_parameters_are_rejected() -> None:
    with pytest.raises(ValidationError, match="must exceed fast"):
        BreakoutStrategy().validate_parameters(
            {"fast_ma_period": 50, "slow_ma_period": 50}
        )


def test_trusted_strategy_package_has_no_io_or_provider_imports() -> None:
    strategy_root = Path(__file__).parents[1] / "src" / "investing_bot" / "strategies"
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
    for path in strategy_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for module in imported:
                if any(module == item or module.startswith(f"{item}.") for item in prohibited):
                    violations.append(f"{path.name}:{node.lineno}:{module}")
    assert violations == []
