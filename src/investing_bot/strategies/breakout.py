"""Explainable range-breakout hypothesis; not a validated trading strategy."""

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
from investing_bot.strategies.trend_pullback import _condition, _inactive_signal, _round


class BreakoutParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fast_ma_period: int = Field(default=20, ge=5, le=100)
    slow_ma_period: int = Field(default=50, ge=20, le=300)
    atr_period: int = Field(default=14, ge=5, le=100)
    volume_period: int = Field(default=20, ge=5, le=100)
    relative_strength_lookback: int = Field(default=20, ge=5, le=120)
    breakout_period: int = Field(default=20, ge=5, le=120)
    structural_low_lookback: int = Field(default=10, ge=2, le=100)
    minimum_average_volume: int = Field(default=500_000, ge=0)
    minimum_relative_strength_pct: float = Field(default=0, ge=-100, le=100)
    minimum_volume_ratio: float = Field(default=1.2, gt=0, le=10)
    forming_distance_atr: float = Field(default=1.0, gt=0, le=10)
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


class BreakoutStrategy:
    @property
    def manifest(self) -> StrategyManifest:
        defaults = BreakoutParameters()
        return StrategyManifest(
            strategy_id="breakout",
            display_name="Range breakout",
            version="0.1.0",
            description=(
                "Research hypothesis requiring a daily uptrend, positive "
                "benchmark-relative strength, and a completed breakout through "
                "prior resistance with volume confirmation."
            ),
            research_status=StrategyResearchStatus.HYPOTHESIS,
            live_alerts_enabled=False,
            required_intervals=(BarInterval.DAY_1,),
            warmup_daily_bars=max(
                defaults.slow_ma_period,
                defaults.relative_strength_lookback + 1,
                defaults.atr_period + 1,
                defaults.volume_period + 1,
                defaults.breakout_period + 1,
            ),
            parameter_schema=BreakoutParameters.model_json_schema(),
            default_parameters=defaults.model_dump(mode="json"),
        )

    def validate_parameters(
        self,
        values: Mapping[str, Any] | BaseModel | None = None,
    ) -> BreakoutParameters:
        if values is None:
            return BreakoutParameters()
        if isinstance(values, BreakoutParameters):
            return values
        if isinstance(values, BaseModel):
            values = values.model_dump()
        return BreakoutParameters.model_validate(values)

    def evaluate(
        self, frame: MarketFrame, parameters: BaseModel
    ) -> StrategySignal:
        if not isinstance(parameters, BreakoutParameters):
            raise TypeError("breakout parameters have the wrong type")
        features = calculate_features(
            frame,
            fast_period=parameters.fast_ma_period,
            slow_period=parameters.slow_ma_period,
            atr_period=parameters.atr_period,
            volume_period=parameters.volume_period,
            relative_strength_lookback=parameters.relative_strength_lookback,
            breakout_period=parameters.breakout_period,
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
            features.breakout_level,
            features.volume_ratio,
        )
        if any(value is None for value in required) or not frame.daily_bars:
            return _inactive_signal(
                self.manifest,
                frame,
                features,
                stale=stale,
                summary="Insufficient completed daily history for breakout research.",
                reason="insufficient_history",
            )
        close = float(features.close)
        fast = float(features.fast_moving_average)
        slow = float(features.slow_moving_average)
        atr = float(features.atr)
        average_volume = float(features.average_volume)
        relative_strength = float(features.relative_strength_pct)
        breakout_level = float(features.breakout_level)
        volume_ratio = float(features.volume_ratio)
        current = frame.daily_bars[-1]
        trend = close > fast > slow
        relative = relative_strength >= parameters.minimum_relative_strength_pct
        liquid = average_volume >= parameters.minimum_average_volume
        near_breakout = close >= breakout_level - parameters.forming_distance_atr * atr
        price_breakout = close > breakout_level and close > current.open
        volume_confirmed = volume_ratio >= parameters.minimum_volume_ratio
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
                "near_prior_resistance",
                near_breakout,
                close,
                f"within {parameters.forming_distance_atr:.2f} ATR of {breakout_level:.2f}",
            ),
            _condition(
                "completed_price_breakout",
                price_breakout,
                close,
                f"completed close above {breakout_level:.2f}",
            ),
            _condition(
                "volume_confirmation",
                volume_confirmed,
                volume_ratio,
                f">= {parameters.minimum_volume_ratio:.2f}x prior average",
                suffix="x",
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
        base_valid = trend and relative and liquid and near_breakout
        confirmed = base_valid and price_breakout and volume_confirmed and intraday
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
                "All breakout-hypothesis conditions passed on completed bars."
                if state is SetupState.CONFIRMED
                else (
                    "Price is near resistance, but breakout confirmation is incomplete."
                    if state is SetupState.FORMING
                    else "The trend, strength, liquidity, or proximity gate failed."
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
        trigger_base = (
            max(current.high, breakout_level)
            if state is SetupState.CONFIRMED
            else breakout_level
        )
        trigger = _round(trigger_base + parameters.entry_buffer_atr * atr)
        zone_high = _round(trigger + parameters.entry_zone_atr * atr)
        recent_low = min(
            bar.low
            for bar in frame.daily_bars[-parameters.structural_low_lookback :]
        )
        stop = _round(
            min(
                recent_low,
                breakout_level - parameters.stop_atr_multiple * atr,
            )
        )
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
                rationale="Lower of prior range support and the ATR boundary.",
            ),
            exit=ExitIntent(
                profit_target=target,
                trailing_atr_multiple=parameters.trailing_atr_multiple,
                maximum_holding_sessions=parameters.maximum_holding_sessions,
            ),
            reward_to_risk=parameters.reward_multiple,
        )
