"""Provider-independent contracts for deterministic strategy research."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from investing_bot.models.provider import BarInterval, Sha256Hex, Ticker


class SetupState(StrEnum):
    FORMING = "forming"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    CLOSED = "closed"


class StrategyResearchStatus(StrEnum):
    HYPOTHESIS = "hypothesis"
    BACKTEST_PENDING = "backtest_pending"
    VALIDATED = "validated"


class StrategyBar(BaseModel):
    """One completed, point-in-time bar exposed to a trusted strategy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Ticker
    interval: BarInterval
    bar_start: AwareDatetime
    bar_end: AwareDatetime
    session_date: date
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: int = Field(ge=0)
    known_available_at: AwareDatetime
    provider_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    repaired: bool = False

    @model_validator(mode="after")
    def validate_completed_bar(self) -> Self:
        if self.bar_end <= self.bar_start:
            raise ValueError("strategy bar must end after it starts")
        if self.low > min(self.open, self.close):
            raise ValueError("strategy bar low cannot exceed open or close")
        if self.high < max(self.open, self.close) or self.high < self.low:
            raise ValueError("strategy bar high is invalid")
        if self.known_available_at < self.bar_end:
            raise ValueError("strategy bar cannot be known before completion")
        return self


class MarketFrame(BaseModel):
    """Immutable market slice containing no information after ``as_of``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Ticker
    benchmark_symbol: Ticker = "SPY"
    provider_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    as_of: AwareDatetime
    input_hash: Sha256Hex
    daily_bars: tuple[StrategyBar, ...]
    benchmark_daily_bars: tuple[StrategyBar, ...]
    intraday_bars: tuple[StrategyBar, ...] = ()

    @model_validator(mode="after")
    def validate_point_in_time_frame(self) -> Self:
        groups = (
            (self.daily_bars, self.symbol, BarInterval.DAY_1),
            (
                self.benchmark_daily_bars,
                self.benchmark_symbol,
                BarInterval.DAY_1,
            ),
            (self.intraday_bars, self.symbol, BarInterval.MINUTE_15),
        )
        for bars, symbol, interval in groups:
            identities: list[tuple[AwareDatetime, AwareDatetime]] = []
            for bar in bars:
                if bar.symbol != symbol or bar.interval is not interval:
                    raise ValueError("strategy frame contains a mismatched bar")
                if bar.provider_id != self.provider_id:
                    raise ValueError("strategy frame cannot blend providers")
                if bar.bar_end > self.as_of or bar.known_available_at > self.as_of:
                    raise ValueError("strategy frame contains future information")
                identities.append((bar.bar_start, bar.bar_end))
            if identities != sorted(identities) or len(set(identities)) != len(
                identities
            ):
                raise ValueError("strategy frame bars must be unique and ordered")
        return self


class StrategyFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    daily_bar_count: int = Field(ge=0)
    benchmark_bar_count: int = Field(ge=0)
    intraday_bar_count: int = Field(ge=0)
    data_through: AwareDatetime | None
    close: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    fast_moving_average: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    slow_moving_average: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    atr: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    average_volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    relative_strength_pct: float | None = Field(default=None, allow_inf_nan=False)
    distance_to_fast_atr: float | None = Field(default=None, allow_inf_nan=False)
    breakout_level: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    volume_ratio: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    intraday_confirmed: bool | None = None


class StrategyCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    condition_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    passed: bool
    observed: str = Field(min_length=1, max_length=160)
    requirement: str = Field(min_length=1, max_length=160)


class StrategyExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1, max_length=500)
    reason_codes: tuple[str, ...] = Field(max_length=30)
    conditions: tuple[StrategyCondition, ...] = Field(max_length=30)


class EntryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_price: float = Field(gt=0, allow_inf_nan=False)
    zone_low: float = Field(gt=0, allow_inf_nan=False)
    zone_high: float = Field(gt=0, allow_inf_nan=False)
    valid_after: AwareDatetime
    order_type: str = Field(default="stop_limit", pattern=r"^[a-z_]+$")

    @model_validator(mode="after")
    def validate_zone(self) -> Self:
        if self.zone_low > self.trigger_price or self.trigger_price > self.zone_high:
            raise ValueError("entry trigger must lie inside its zone")
        return self


class StopIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invalidation_price: float = Field(gt=0, allow_inf_nan=False)
    rationale: str = Field(min_length=1, max_length=300)


class ExitIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profit_target: float = Field(gt=0, allow_inf_nan=False)
    trailing_atr_multiple: float = Field(gt=0, allow_inf_nan=False)
    maximum_holding_sessions: int = Field(ge=1, le=500)
    exit_on_trend_failure: bool = True


class StrategySignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Ticker
    strategy_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    strategy_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    state: SetupState
    stale: bool
    confirmation_blocked: bool
    features: StrategyFeatures
    explanation: StrategyExplanation
    entry: EntryIntent | None = None
    stop: StopIntent | None = None
    exit: ExitIntent | None = None
    reward_to_risk: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_intents(self) -> Self:
        intents = (self.entry, self.stop, self.exit, self.reward_to_risk)
        if self.state in {SetupState.FORMING, SetupState.CONFIRMED}:
            if any(item is None for item in intents):
                raise ValueError("forming and confirmed setups require complete intents")
            assert self.entry is not None and self.stop is not None
            if self.stop.invalidation_price >= self.entry.zone_low:
                raise ValueError("long setup stop must be below the entry zone")
        elif any(item is not None for item in intents):
            raise ValueError("inactive setup states cannot retain entry intents")
        if self.stale and not self.confirmation_blocked:
            raise ValueError("stale market data must block confirmation")
        return self


class StrategyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    description: str = Field(min_length=1, max_length=500)
    research_status: StrategyResearchStatus
    live_alerts_enabled: bool = False
    required_intervals: tuple[BarInterval, ...]
    warmup_daily_bars: int = Field(ge=2, le=2_000)
    parameter_schema: dict[str, Any]
    default_parameters: dict[str, Any]

    @model_validator(mode="after")
    def prevent_unvalidated_live_alerts(self) -> Self:
        if (
            self.live_alerts_enabled
            and self.research_status is not StrategyResearchStatus.VALIDATED
        ):
            raise ValueError("only validated strategies may enable live alerts")
        return self
