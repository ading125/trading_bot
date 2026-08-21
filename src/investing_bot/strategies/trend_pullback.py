"""Explainable trend-pullback hypothesis; not a validated trading strategy."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investing_bot.models import (
    BarInterval,
    EntryIntent,
    ExitIntent,
    MarketFrame,
    SetupState,
    StopIntent,
    StrategyCondition,
    StrategyExplanation,
    StrategyManifest,
    StrategyResearchStatus,
    StrategySignal,
)
from investing_bot.strategies.indicators import calculate_features


class TrendPullbackParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fast_ma_period: int = Field(default=20, ge=5, le=100)
    slow_ma_period: int = Field(default=50, ge=20, le=300)
    atr_period: int = Field(default=14, ge=5, le=100)
    volume_period: int = Field(default=20, ge=5, le=100)
    relative_strength_lookback: int = Field(default=20, ge=5, le=120)
    structural_low_lookback: int = Field(default=10, ge=2, le=100)
    minimum_average_volume: int = Field(default=500_000, ge=0)
    minimum_relative_strength_pct: float = Field(default=0, ge=-100, le=100)
    pullback_tolerance_atr: float = Field(default=1.0, gt=0, le=5)
    entry_buffer_atr: float = Field(default=0.05, ge=0, le=2)
    entry_zone_atr: float = Field(default=0.25, gt=0, le=3)
    stop_atr_multiple: float = Field(default=1.5, gt=0, le=10)
    reward_multiple: float = Field(default=2.0, gt=0, le=20)
    trailing_atr_multiple: float = Field(default=2.0, gt=0, le=10)
    maximum_holding_sessions: int = Field(default=20, ge=1, le=250)
    maximum_staleness_days: int = Field(default=5, ge=1, le=30)
    require_intraday_confirmation: bool = False

    @model_validator(mode="after")
    def slow_average_exceeds_fast(self) -> Self:
        if self.slow_ma_period <= self.fast_ma_period:
            raise ValueError("slow moving-average period must exceed fast period")
        return self


class TrendPullbackStrategy:
    @property
    def manifest(self) -> StrategyManifest:
        defaults = TrendPullbackParameters()
        return StrategyManifest(
            strategy_id="trend_pullback",
            display_name="Trend pullback",
            version="0.1.0",
            description=(
                "Research hypothesis requiring an established daily uptrend, "
                "positive benchmark-relative strength, a controlled pullback, "
                "and completed-bar recovery."
            ),
            research_status=StrategyResearchStatus.HYPOTHESIS,
            live_alerts_enabled=False,
            required_intervals=(BarInterval.DAY_1,),
            warmup_daily_bars=max(
                defaults.slow_ma_period,
                defaults.relative_strength_lookback + 1,
                defaults.atr_period + 1,
                defaults.volume_period + 1,
            ),
            parameter_schema=TrendPullbackParameters.model_json_schema(),
            default_parameters=defaults.model_dump(mode="json"),
        )

    def validate_parameters(
        self,
        values: Mapping[str, Any] | BaseModel | None = None,
    ) -> TrendPullbackParameters:
        if values is None:
            return TrendPullbackParameters()
        if isinstance(values, TrendPullbackParameters):
            return values
        if isinstance(values, BaseModel):
            values = values.model_dump()
        return TrendPullbackParameters.model_validate(values)

    def evaluate(
        self, frame: MarketFrame, parameters: BaseModel
    ) -> StrategySignal:
        if not isinstance(parameters, TrendPullbackParameters):
            raise TypeError("trend-pullback parameters have the wrong type")
        features = calculate_features(
            frame,
            fast_period=parameters.fast_ma_period,
            slow_period=parameters.slow_ma_period,
            atr_period=parameters.atr_period,
            volume_period=parameters.volume_period,
            relative_strength_lookback=parameters.relative_strength_lookback,
            breakout_period=20,
        )
        stale = bool(
            features.data_through is None
            or frame.as_of - features.data_through
            > timedelta(days=parameters.maximum_staleness_days)
        )
        required = (
            features.close,
            features.fast_moving_average,
            features.slow_moving_average,
            features.atr,
            features.average_volume,
            features.relative_strength_pct,
        )
        if any(value is None for value in required) or not frame.daily_bars:
            return _inactive_signal(
                self.manifest,
                frame,
                features,
                stale=stale,
                summary="Insufficient completed daily history for trend-pullback research.",
                reason="insufficient_history",
            )
        close = float(features.close)
        fast = float(features.fast_moving_average)
        slow = float(features.slow_moving_average)
        atr = float(features.atr)
        average_volume = float(features.average_volume)
        relative_strength = float(features.relative_strength_pct)
        current = frame.daily_bars[-1]
        previous = frame.daily_bars[-2]
        trend = close > fast > slow
        relative = relative_strength >= parameters.minimum_relative_strength_pct
        liquid = average_volume >= parameters.minimum_average_volume
        pullback = (
            current.low <= fast + parameters.pullback_tolerance_atr * atr
            and close >= fast - parameters.pullback_tolerance_atr * atr
        )
        recovery = close > previous.close and close > current.open
        intraday = (
            not parameters.require_intraday_confirmation
            or features.intraday_confirmed is True
        )
        conditions = (
            _condition("daily_uptrend", trend, close, f"close > {fast:.2f} > {slow:.2f}"),
            _condition(
                "relative_strength",
                relative,
                relative_strength,
                f">= {parameters.minimum_relative_strength_pct:.2f}% vs SPY",
                suffix="%",
            ),
            _condition(
                "minimum_liquidity",
                liquid,
                average_volume,
                f">= {parameters.minimum_average_volume:,} average shares",
            ),
            _condition(
                "controlled_pullback",
                pullback,
                current.low,
                f"within {parameters.pullback_tolerance_atr:.2f} ATR of fast average",
            ),
            _condition(
                "completed_bar_recovery",
                recovery,
                close,
                "close above prior close and current open",
            ),
            StrategyCondition(
                condition_id="intraday_confirmation",
                passed=intraday,
                observed=(
                    "not required"
                    if not parameters.require_intraday_confirmation
                    else str(features.intraday_confirmed).lower()
                ),
                requirement=(
                    "disabled by parameters"
                    if not parameters.require_intraday_confirmation
                    else "completed 15-minute recovery"
                ),
            ),
        )
        base_valid = trend and relative and liquid
        confirmed = base_valid and pullback and recovery and intraday
        if not base_valid:
            state = SetupState.INVALIDATED
        elif confirmed and not stale:
            state = SetupState.CONFIRMED
        else:
            state = SetupState.FORMING
        reasons = tuple(item.condition_id for item in conditions if not item.passed)
        if stale:
            reasons = (*reasons, "stale_data")
        explanation = StrategyExplanation(
            summary=(
                "All hypothesis conditions passed on completed bars."
                if state is SetupState.CONFIRMED
                else (
                    "The trend remains eligible, but confirmation is incomplete."
                    if state is SetupState.FORMING
                    else "The trend, relative-strength, or liquidity gate failed."
                )
            ),
            reason_codes=tuple(dict.fromkeys(reasons)),
            conditions=conditions,
        )
        if state is SetupState.INVALIDATED:
            return StrategySignal(
                symbol=frame.symbol,
                strategy_id=self.manifest.strategy_id,
                strategy_version=self.manifest.version,
                state=state,
                stale=stale,
                confirmation_blocked=stale,
                features=features,
                explanation=explanation,
            )
        recent_low = min(
            bar.low
            for bar in frame.daily_bars[-parameters.structural_low_lookback :]
        )
        trigger = _round(current.high + parameters.entry_buffer_atr * atr)
        zone_high = _round(trigger + parameters.entry_zone_atr * atr)
        stop = _round(min(recent_low, close - parameters.stop_atr_multiple * atr))
        risk = trigger - stop
        target = _round(trigger + parameters.reward_multiple * risk)
        return StrategySignal(
            symbol=frame.symbol,
            strategy_id=self.manifest.strategy_id,
            strategy_version=self.manifest.version,
            state=state,
            stale=stale,
            confirmation_blocked=stale,
            features=features,
            explanation=explanation,
            entry=EntryIntent(
                trigger_price=trigger,
                zone_low=trigger,
                zone_high=zone_high,
                valid_after=current.bar_end,
            ),
            stop=StopIntent(
                invalidation_price=stop,
                rationale="Lower of the recent structural low and ATR boundary.",
            ),
            exit=ExitIntent(
                profit_target=target,
                trailing_atr_multiple=parameters.trailing_atr_multiple,
                maximum_holding_sessions=parameters.maximum_holding_sessions,
            ),
            reward_to_risk=parameters.reward_multiple,
        )


def _condition(
    condition_id: str,
    passed: bool,
    observed: float,
    requirement: str,
    *,
    suffix: str = "",
) -> StrategyCondition:
    return StrategyCondition(
        condition_id=condition_id,
        passed=passed,
        observed=f"{observed:,.2f}{suffix}",
        requirement=requirement,
    )


def _inactive_signal(
    manifest: StrategyManifest,
    frame: MarketFrame,
    features,
    *,
    stale: bool,
    summary: str,
    reason: str,
) -> StrategySignal:
    return StrategySignal(
        symbol=frame.symbol,
        strategy_id=manifest.strategy_id,
        strategy_version=manifest.version,
        state=SetupState.INVALIDATED,
        stale=stale,
        confirmation_blocked=stale,
        features=features,
        explanation=StrategyExplanation(
            summary=summary,
            reason_codes=(reason,),
            conditions=(
                StrategyCondition(
                    condition_id="completed_history",
                    passed=False,
                    observed=f"{features.daily_bar_count} daily bars",
                    requirement=f"at least {manifest.warmup_daily_bars} daily bars",
                ),
            ),
        ),
    )


def _round(value: float) -> float:
    return round(float(value), 6)
