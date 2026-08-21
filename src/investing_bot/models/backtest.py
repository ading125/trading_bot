"""Canonical contracts for deterministic point-in-time strategy research."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from math import isclose
from typing import Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from investing_bot.models.provider import Sha256Hex, Ticker


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELED = "canceled"


class OrderReason(StrEnum):
    ENTRY = "entry"
    PROTECTIVE_STOP = "protective_stop"
    PROFIT_TARGET = "profit_target"
    TRAILING_STOP = "trailing_stop"
    TIME_EXIT = "time_exit"
    TREND_FAILURE = "trend_failure"
    END_OF_TEST = "end_of_test"


class MarketRegime(StrEnum):
    UNKNOWN = "unknown"
    BULLISH = "bullish"
    BEARISH = "bearish"
    HIGH_VOLATILITY = "high_volatility"
    SIDEWAYS = "sideways"


class ResearchPeriodName(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    OUT_OF_SAMPLE = "out_of_sample"


class BacktestCostModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commission_bps: float = Field(default=1.0, ge=0, le=100, allow_inf_nan=False)
    slippage_bps: float = Field(default=5.0, ge=0, le=100, allow_inf_nan=False)
    risk_free_rate_annual: float = Field(
        default=0.0, ge=-0.1, le=0.5, allow_inf_nan=False
    )


class BacktestPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ResearchPeriodName
    start: date
    end: date

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end <= self.start:
            raise ValueError("backtest period end must be after start")
        return self


class WalkForwardPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    development: BacktestPeriod
    validation: BacktestPeriod
    out_of_sample: BacktestPeriod

    @model_validator(mode="after")
    def validate_ordered_periods(self) -> Self:
        expected = (
            (self.development, ResearchPeriodName.DEVELOPMENT),
            (self.validation, ResearchPeriodName.VALIDATION),
            (self.out_of_sample, ResearchPeriodName.OUT_OF_SAMPLE),
        )
        if any(period.name is not name for period, name in expected):
            raise ValueError("walk-forward period names do not match their roles")
        if self.development.end >= self.validation.start:
            raise ValueError("development and validation periods cannot overlap")
        if self.validation.end >= self.out_of_sample.start:
            raise ValueError("validation and out-of-sample periods cannot overlap")
        return self


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    strategy_parameters: dict[str, Any] = Field(default_factory=dict)
    symbols: tuple[Ticker, ...] = Field(min_length=1, max_length=500)
    benchmark_symbol: Ticker = "SPY"
    provider_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    period: BacktestPeriod
    starting_capital: float = Field(
        default=100_000.0, gt=0, allow_inf_nan=False
    )
    allocation_per_trade: float = Field(
        default=0.1, gt=0, le=1, allow_inf_nan=False
    )
    costs: BacktestCostModel = Field(default_factory=BacktestCostModel)
    current_constituents_only: bool = True

    @model_validator(mode="after")
    def validate_symbols(self) -> Self:
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("backtest symbols cannot contain duplicates")
        return self


class SimulatedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: Sha256Hex
    symbol: Ticker
    side: OrderSide
    reason: OrderReason
    status: OrderStatus
    submitted_at: AwareDatetime
    eligible_after: AwareDatetime
    signal_bar_end: AwareDatetime | None
    trigger_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    limit_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    requested_quantity: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    completed_at: AwareDatetime | None = None
    fill_id: Sha256Hex | None = None
    rejection_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.eligible_after <= self.submitted_at:
            raise ValueError("an order cannot be eligible at its submission timestamp")
        if self.status is OrderStatus.FILLED and (
            self.fill_id is None or self.completed_at is None
        ):
            raise ValueError("filled order requires a fill and completion time")
        if self.status is OrderStatus.REJECTED and not self.rejection_reason:
            raise ValueError("rejected order requires a reason")
        if self.status is OrderStatus.PENDING and self.completed_at is not None:
            raise ValueError("pending order cannot be completed")
        return self


class SimulatedFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fill_id: Sha256Hex
    order_id: Sha256Hex
    symbol: Ticker
    side: OrderSide
    filled_at: AwareDatetime
    reference_price: float = Field(gt=0, allow_inf_nan=False)
    execution_price: float = Field(gt=0, allow_inf_nan=False)
    quantity: float = Field(gt=0, allow_inf_nan=False)
    notional: float = Field(gt=0, allow_inf_nan=False)
    commission: float = Field(ge=0, allow_inf_nan=False)
    slippage_cost: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_notional(self) -> Self:
        if not isclose(
            self.notional,
            self.execution_price * self.quantity,
            rel_tol=1e-8,
            abs_tol=1e-6,
        ):
            raise ValueError("fill notional does not reconcile")
        return self


class BacktestTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_id: Sha256Hex
    symbol: Ticker
    entry_order_id: Sha256Hex
    entry_fill_id: Sha256Hex
    exit_order_id: Sha256Hex
    exit_fill_id: Sha256Hex
    entry_signal_at: AwareDatetime
    entered_at: AwareDatetime
    exited_at: AwareDatetime
    entry_price: float = Field(gt=0, allow_inf_nan=False)
    exit_price: float = Field(gt=0, allow_inf_nan=False)
    quantity: float = Field(gt=0, allow_inf_nan=False)
    gross_pnl: float = Field(allow_inf_nan=False)
    net_pnl: float = Field(allow_inf_nan=False)
    return_pct: float = Field(allow_inf_nan=False)
    costs: float = Field(ge=0, allow_inf_nan=False)
    holding_sessions: int = Field(ge=1)
    exit_reason: OrderReason
    entry_regime: MarketRegime

    @model_validator(mode="after")
    def enforce_signal_then_fill(self) -> Self:
        if self.entered_at <= self.entry_signal_at:
            raise ValueError("close-derived entry cannot fill at the signal close")
        if self.exited_at < self.entered_at:
            raise ValueError("trade exit cannot precede entry")
        return self


class EquityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    recorded_at: AwareDatetime
    cash: float = Field(ge=0, allow_inf_nan=False)
    market_value: float = Field(ge=0, allow_inf_nan=False)
    equity: float = Field(ge=0, allow_inf_nan=False)
    drawdown_pct: float = Field(ge=-100, le=0, allow_inf_nan=False)
    exposed: bool


class RegimePerformance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    regime: MarketRegime
    trade_count: int = Field(ge=0)
    win_rate: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    net_pnl: float = Field(allow_inf_nan=False)
    expectancy_pct: float | None = Field(default=None, allow_inf_nan=False)


class YearPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int = Field(ge=1900, le=3000)
    trade_count: int = Field(ge=0)
    net_pnl: float = Field(allow_inf_nan=False)
    return_pct: float = Field(allow_inf_nan=False)


class BacktestMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_return_pct: float = Field(allow_inf_nan=False)
    cagr_pct: float | None = Field(default=None, allow_inf_nan=False)
    win_rate: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    average_win_pct: float | None = Field(default=None, allow_inf_nan=False)
    average_loss_pct: float | None = Field(default=None, allow_inf_nan=False)
    payoff_ratio: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expectancy_pct: float | None = Field(default=None, allow_inf_nan=False)
    profit_factor: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    maximum_drawdown_pct: float = Field(ge=-100, le=0, allow_inf_nan=False)
    recovery_sessions: int | None = Field(default=None, ge=0)
    annualized_volatility_pct: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    sharpe_ratio: float | None = Field(default=None, allow_inf_nan=False)
    exposure_pct: float = Field(ge=0, le=100, allow_inf_nan=False)
    turnover: float = Field(ge=0, allow_inf_nan=False)
    trade_count: int = Field(ge=0)
    average_holding_sessions: float | None = Field(
        default=None, ge=1, allow_inf_nan=False
    )
    total_costs: float = Field(ge=0, allow_inf_nan=False)
    regimes: tuple[RegimePerformance, ...]
    years: tuple[YearPerformance, ...]


class BenchmarkComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Ticker
    start_close: float = Field(gt=0, allow_inf_nan=False)
    end_close: float = Field(gt=0, allow_inf_nan=False)
    total_return_pct: float = Field(allow_inf_nan=False)
    cagr_pct: float | None = Field(default=None, allow_inf_nan=False)
    maximum_drawdown_pct: float = Field(ge=-100, le=0, allow_inf_nan=False)


class BacktestReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=200)
    methodology: tuple[str, ...] = Field(min_length=1)
    disclosures: tuple[str, ...] = Field(min_length=1)
    ai_outcomes_included: bool = False
    survivorship_bias_warning: bool

    @model_validator(mode="after")
    def separate_ai_history(self) -> Self:
        if self.ai_outcomes_included:
            raise ValueError("deterministic backtests cannot include reconstructed AI outcomes")
        return self


class BacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_hash: Sha256Hex
    strategy_id: str
    strategy_version: str
    parameters: dict[str, Any]
    parameters_hash: Sha256Hex
    data_hash: Sha256Hex
    provider_id: str
    period: BacktestPeriod
    starting_capital: float = Field(gt=0, allow_inf_nan=False)
    ending_cash: float = Field(ge=0, allow_inf_nan=False)
    ending_equity: float = Field(ge=0, allow_inf_nan=False)
    total_realized_pnl: float = Field(allow_inf_nan=False)
    total_costs: float = Field(ge=0, allow_inf_nan=False)
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[SimulatedFill, ...]
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: BacktestMetrics
    benchmark: BenchmarkComparison
    report: BacktestReport

    @model_validator(mode="after")
    def reconcile_result(self) -> Self:
        if not isclose(self.ending_cash, self.ending_equity, abs_tol=1e-5):
            raise ValueError("all positions must be closed at the end of a backtest")
        expected_cash = self.starting_capital + sum(
            trade.net_pnl for trade in self.trades
        )
        if not isclose(self.ending_cash, expected_cash, rel_tol=1e-8, abs_tol=1e-4):
            raise ValueError("cash does not reconcile to closed-trade P&L")
        if not isclose(
            self.total_realized_pnl,
            sum(trade.net_pnl for trade in self.trades),
            rel_tol=1e-8,
            abs_tol=1e-5,
        ):
            raise ValueError("realized P&L does not reconcile")
        expected_costs = sum(
            fill.commission + fill.slippage_cost for fill in self.fills
        )
        if not isclose(self.total_costs, expected_costs, rel_tol=1e-8, abs_tol=1e-5):
            raise ValueError("backtest costs do not reconcile")
        order_by_id = {order.order_id: order for order in self.orders}
        for fill in self.fills:
            order = order_by_id.get(fill.order_id)
            if order is None or order.fill_id != fill.fill_id:
                raise ValueError("fill does not reference a filled order")
            if fill.filled_at < order.eligible_after:
                raise ValueError("order filled before it became eligible")
        return self


class ParameterCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    parameters: dict[str, Any]


class WalkForwardExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    symbols: tuple[Ticker, ...] = Field(min_length=1, max_length=500)
    benchmark_symbol: Ticker = "SPY"
    provider_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    plan: WalkForwardPlan
    candidates: tuple[ParameterCandidate, ...] = Field(min_length=1, max_length=100)
    starting_capital: float = Field(default=100_000, gt=0, allow_inf_nan=False)
    allocation_per_trade: float = Field(default=0.1, gt=0, le=1)
    costs: BacktestCostModel = Field(default_factory=BacktestCostModel)
    minimum_trades_per_holdout: int = Field(default=20, ge=1, le=10_000)
    maximum_acceptable_drawdown_pct: float = Field(default=25, gt=0, le=100)
    current_constituents_only: bool = True

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("parameter candidate IDs must be unique")
        return self


class ParameterTrialReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: ParameterCandidate
    parameters_hash: Sha256Hex
    development_run_hash: Sha256Hex
    validation_run_hash: Sha256Hex
    out_of_sample_run_hash: Sha256Hex | None = None
    development_metrics: BacktestMetrics
    validation_metrics: BacktestMetrics
    out_of_sample_metrics: BacktestMetrics | None = None

    @model_validator(mode="after")
    def validate_out_of_sample_pair(self) -> Self:
        if (self.out_of_sample_run_hash is None) != (
            self.out_of_sample_metrics is None
        ):
            raise ValueError("out-of-sample hash and metrics must be stored together")
        return self


class ResearchAcceptanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    selected_candidate_id: str
    nearby_parameter_sets_tested: int = Field(ge=0)
    nearby_parameter_sets_positive: int = Field(ge=0)
    parameter_stability_met: bool
    validation_positive: bool
    out_of_sample_positive: bool
    minimum_trade_count_met: bool
    drawdown_limit_met: bool
    reasons: tuple[str, ...]
    live_alerts_may_be_enabled: bool

    @model_validator(mode="after")
    def validate_gate(self) -> Self:
        checks = (
            self.validation_positive,
            self.out_of_sample_positive,
            self.minimum_trade_count_met,
            self.drawdown_limit_met,
            self.parameter_stability_met,
        )
        expected_stability = (
            self.nearby_parameter_sets_tested >= 3
            and self.nearby_parameter_sets_positive >= 2
        )
        if self.parameter_stability_met != expected_stability:
            raise ValueError("parameter-stability result does not match nearby trials")
        if self.passed != all(checks):
            raise ValueError("acceptance result does not match its checks")
        if self.live_alerts_may_be_enabled != self.passed:
            raise ValueError("live-alert eligibility must match research acceptance")
        return self


class WalkForwardExperimentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_hash: Sha256Hex
    strategy_id: str
    strategy_version: str
    provider_id: str
    plan: WalkForwardPlan
    trials: tuple[ParameterTrialReport, ...]
    acceptance: ResearchAcceptanceReport
    ai_outcomes_included: bool = False
    disclosures: tuple[str, ...]

    @model_validator(mode="after")
    def forbid_reconstructed_ai(self) -> Self:
        if self.ai_outcomes_included:
            raise ValueError("walk-forward research cannot reconstruct AI history")
        selected = [
            trial
            for trial in self.trials
            if trial.candidate.candidate_id
            == self.acceptance.selected_candidate_id
        ]
        if len(selected) != 1 or selected[0].out_of_sample_metrics is None:
            raise ValueError("selected parameter candidate requires one final holdout run")
        if any(
            trial.out_of_sample_metrics is not None and trial is not selected[0]
            for trial in self.trials
        ):
            raise ValueError("final holdout may run only for the selected candidate")
        return self
