"""Event-driven long-only simulator using the live strategy interface."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
from typing import Any

from pydantic import BaseModel

from investing_bot.backtesting.metrics import (
    benchmark_comparison,
    calculate_metrics,
    classify_regimes,
)
from investing_bot.models import (
    BacktestReport,
    BacktestRequest,
    BacktestResult,
    BacktestTrade,
    BarInterval,
    EquityPoint,
    MarketFrame,
    MarketRegime,
    OrderReason,
    OrderSide,
    OrderStatus,
    SetupState,
    SimulatedFill,
    SimulatedOrder,
    StrategyBar,
    StrategySignal,
)
from investing_bot.strategies import Strategy


ENGINE_VERSION = "1.0.0"


class BacktestInvariantError(ValueError):
    """Raised when inputs or strategy output violate point-in-time rules."""


@dataclass(slots=True)
class _PendingEntry:
    order_id: str
    signal_at: Any
    trigger: float
    limit: float
    stop: float
    target: float
    trailing_atr_multiple: float
    maximum_holding_sessions: int
    exit_on_trend_failure: bool
    entry_atr: float
    regime: MarketRegime


@dataclass(slots=True)
class _Position:
    symbol: str
    entry_order_id: str
    entry_fill: SimulatedFill
    entry_signal_at: Any
    quantity: float
    initial_stop: float
    active_stop: float
    target: float
    trailing_atr_multiple: float
    maximum_holding_sessions: int
    exit_on_trend_failure: bool
    highest_price: float
    holding_sessions: int
    entry_regime: MarketRegime
    pending_exit_reason: OrderReason | None = None
    pending_exit_signal_at: Any | None = None


class BacktestEngine:
    """Run deterministic bar events without network, filesystem, or LLM access."""

    def run(
        self,
        request: BacktestRequest,
        *,
        strategy: Strategy,
        bars_by_symbol: dict[str, tuple[StrategyBar, ...]],
        benchmark_bars: tuple[StrategyBar, ...],
    ) -> BacktestResult:
        manifest = strategy.manifest
        if manifest.strategy_id != request.strategy_id:
            raise BacktestInvariantError("request strategy does not match plugin")
        parameters = strategy.validate_parameters(request.strategy_parameters)
        normalized_parameters = parameters.model_dump(mode="json")
        self._validate_data(request, bars_by_symbol, benchmark_bars)
        period_benchmark = tuple(
            bar
            for bar in benchmark_bars
            if request.period.start <= bar.session_date <= request.period.end
        )
        if len(period_benchmark) < 2:
            raise BacktestInvariantError("backtest period requires benchmark history")
        data_hash = _data_hash(bars_by_symbol, benchmark_bars, request)
        parameters_hash = _hash(normalized_parameters)
        history_limit = max(manifest.warmup_daily_bars + 10, 400)
        request_identity = request.model_dump(mode="json")
        request_identity["strategy_parameters"] = normalized_parameters
        run_hash = _hash(
            {
                "engine_version": ENGINE_VERSION,
                "strategy_id": manifest.strategy_id,
                "strategy_version": manifest.version,
                "parameters_hash": parameters_hash,
                "data_hash": data_hash,
                "request": request_identity,
            }
        )
        regimes = classify_regimes(benchmark_bars)
        symbol_bars = {
            symbol: {bar.session_date: bar for bar in bars}
            for symbol, bars in bars_by_symbol.items()
        }
        last_period_date = {
            symbol: max(
                bar.session_date
                for bar in bars
                if request.period.start <= bar.session_date <= request.period.end
            )
            for symbol, bars in bars_by_symbol.items()
            if any(
                request.period.start <= bar.session_date <= request.period.end
                for bar in bars
            )
        }

        cash = request.starting_capital
        peak_equity = cash
        orders: dict[str, SimulatedOrder] = {}
        fills: list[SimulatedFill] = []
        trades: list[BacktestTrade] = []
        equity_curve: list[EquityPoint] = []
        pending_entries: dict[str, _PendingEntry] = {}
        positions: dict[str, _Position] = {}
        counter = 0

        def next_id(kind: str, symbol: str, timestamp: Any) -> str:
            nonlocal counter
            counter += 1
            return _hash(
                {
                    "run_hash": run_hash,
                    "kind": kind,
                    "symbol": symbol,
                    "timestamp": timestamp.isoformat(),
                    "counter": counter,
                }
            )

        for clock_bar in period_benchmark:
            session = clock_bar.session_date
            entered_this_session: set[str] = set()
            current_bars = {
                symbol: by_date[session]
                for symbol, by_date in symbol_bars.items()
                if session in by_date
            }
            for symbol in sorted(current_bars):
                bar = current_bars[symbol]
                position = positions.get(symbol)
                if position is not None:
                    exit_reason, reference_price, fill_at, submitted_at = _exit_event(
                        position, bar
                    )
                    if exit_reason is not None:
                        cash = self._close_position(
                            request=request,
                            position=position,
                            reason=exit_reason,
                            reference_price=reference_price,
                            filled_at=fill_at,
                            submitted_at=submitted_at,
                            cash=cash,
                            orders=orders,
                            fills=fills,
                            trades=trades,
                            next_id=next_id,
                        )
                        del positions[symbol]

                pending = pending_entries.pop(symbol, None)
                if pending is not None and symbol not in positions:
                    cash, position = self._process_entry(
                        request=request,
                        bar=bar,
                        pending=pending,
                        cash=cash,
                        positions=positions,
                        orders=orders,
                        fills=fills,
                        next_id=next_id,
                    )
                    if position is not None:
                        positions[symbol] = position
                        entered_this_session.add(symbol)

            for symbol in sorted(current_bars):
                bar = current_bars[symbol]
                signal = self._signal_at_clock(
                    request=request,
                    strategy=strategy,
                    parameters=parameters,
                    symbol=symbol,
                    clock_bar=bar,
                    symbol_history=bars_by_symbol[symbol],
                    benchmark_history=benchmark_bars,
                    history_limit=history_limit,
                    data_hash=data_hash,
                )
                self._audit_signal(signal, strategy, bar)
                position = positions.get(symbol)
                if position is None:
                    if (
                        symbol not in pending_entries
                        and signal.state is SetupState.CONFIRMED
                        and not signal.confirmation_blocked
                        and signal.entry is not None
                        and signal.stop is not None
                        and signal.exit is not None
                        and signal.features.atr is not None
                        and session != last_period_date.get(symbol)
                    ):
                        order_id = next_id("entry_order", symbol, bar.bar_end)
                        orders[order_id] = SimulatedOrder(
                            order_id=order_id,
                            symbol=symbol,
                            side=OrderSide.BUY,
                            reason=OrderReason.ENTRY,
                            status=OrderStatus.PENDING,
                            submitted_at=bar.bar_end,
                            eligible_after=bar.bar_end + timedelta(microseconds=1),
                            signal_bar_end=bar.bar_end,
                            trigger_price=signal.entry.trigger_price,
                            limit_price=signal.entry.zone_high,
                        )
                        pending_entries[symbol] = _PendingEntry(
                            order_id=order_id,
                            signal_at=bar.bar_end,
                            trigger=signal.entry.trigger_price,
                            limit=signal.entry.zone_high,
                            stop=signal.stop.invalidation_price,
                            target=signal.exit.profit_target,
                            trailing_atr_multiple=signal.exit.trailing_atr_multiple,
                            maximum_holding_sessions=(
                                signal.exit.maximum_holding_sessions
                            ),
                            exit_on_trend_failure=signal.exit.exit_on_trend_failure,
                            entry_atr=signal.features.atr,
                            regime=regimes.get(session, MarketRegime.UNKNOWN),
                        )
                else:
                    if symbol not in entered_this_session:
                        position.holding_sessions += 1
                    position.highest_price = max(position.highest_price, bar.high)
                    current_atr = signal.features.atr or 0
                    if current_atr > 0:
                        position.active_stop = max(
                            position.active_stop,
                            position.highest_price
                            - position.trailing_atr_multiple * current_atr,
                        )
                    if (
                        position.holding_sessions
                        >= position.maximum_holding_sessions
                    ):
                        position.pending_exit_reason = OrderReason.TIME_EXIT
                        position.pending_exit_signal_at = bar.bar_end
                    elif (
                        position.exit_on_trend_failure
                        and signal.features.close is not None
                        and signal.features.slow_moving_average is not None
                        and signal.features.close
                        < signal.features.slow_moving_average
                    ):
                        position.pending_exit_reason = OrderReason.TREND_FAILURE
                        position.pending_exit_signal_at = bar.bar_end

                if session == last_period_date.get(symbol):
                    pending = pending_entries.pop(symbol, None)
                    if pending is not None:
                        orders[pending.order_id] = _completed_order(
                            orders[pending.order_id],
                            status=OrderStatus.CANCELED,
                            completed_at=bar.bar_end,
                            rejection_reason=None,
                        )
                    position = positions.get(symbol)
                    if position is not None:
                        cash = self._close_position(
                            request=request,
                            position=position,
                            reason=OrderReason.END_OF_TEST,
                            reference_price=bar.close,
                            filled_at=bar.bar_end,
                            submitted_at=bar.bar_end - timedelta(microseconds=2),
                            cash=cash,
                            orders=orders,
                            fills=fills,
                            trades=trades,
                            next_id=next_id,
                        )
                        del positions[symbol]

            market_value = 0.0
            for symbol, position in positions.items():
                bars = symbol_bars[symbol]
                available = [item for day, item in bars.items() if day <= session]
                if available:
                    market_value += position.quantity * available[-1].close
            equity = max(cash + market_value, 0)
            peak_equity = max(peak_equity, equity)
            drawdown = (equity / peak_equity - 1) * 100 if peak_equity else 0
            equity_curve.append(
                EquityPoint(
                    session_date=session,
                    recorded_at=clock_bar.bar_end,
                    cash=max(cash, 0),
                    market_value=max(market_value, 0),
                    equity=equity,
                    drawdown_pct=drawdown,
                    exposed=bool(positions),
                )
            )

        final_timestamp = period_benchmark[-1].bar_end
        for symbol, pending in tuple(pending_entries.items()):
            orders[pending.order_id] = _completed_order(
                orders[pending.order_id],
                status=OrderStatus.CANCELED,
                completed_at=final_timestamp,
                rejection_reason=None,
            )
            del pending_entries[symbol]
        if positions:
            raise BacktestInvariantError("positions remained open after final liquidation")

        order_values = tuple(orders.values())
        fill_values = tuple(fills)
        trade_values = tuple(trades)
        curve_values = tuple(equity_curve)
        total_costs = sum(fill.commission + fill.slippage_cost for fill in fills)
        metrics = calculate_metrics(
            starting_capital=request.starting_capital,
            equity_curve=curve_values,
            trades=trade_values,
            total_costs=total_costs,
            total_fill_notional=sum(fill.notional for fill in fills),
            costs=request.costs,
        )
        report = BacktestReport(
            title=(
                f"{manifest.display_name} deterministic {request.period.name.value} report"
            ),
            methodology=(
                "Signals use only completed adjusted daily bars available at the simulated clock.",
                "Close-derived entries become eligible after the signal close and can fill only on a later bar.",
                "Long-only fractional positions use fixed equity allocation without leverage.",
                "If a stop and target are both touched in one daily bar, the stop is applied first.",
                (
                    f"Costs include {request.costs.commission_bps:.2f} bps commission "
                    f"and {request.costs.slippage_bps:.2f} bps adverse slippage per fill."
                ),
            ),
            disclosures=(
                "This report tests deterministic price rules only; historical AI selections are not reconstructed.",
                "Adjusted daily bars cannot reproduce intraday path ordering beyond the documented conservative rule.",
                "Results are research estimates, not guarantees or executable brokerage records.",
                (
                    "The requested universe uses current constituents and is survivorship-biased."
                    if request.current_constituents_only
                    else "Universe membership must be independently verified as point-in-time."
                ),
            ),
            ai_outcomes_included=False,
            survivorship_bias_warning=request.current_constituents_only,
        )
        ending_cash = cash
        return BacktestResult(
            run_hash=run_hash,
            strategy_id=manifest.strategy_id,
            strategy_version=manifest.version,
            parameters=normalized_parameters,
            parameters_hash=parameters_hash,
            data_hash=data_hash,
            provider_id=request.provider_id,
            period=request.period,
            starting_capital=request.starting_capital,
            ending_cash=ending_cash,
            ending_equity=ending_cash,
            total_realized_pnl=sum(trade.net_pnl for trade in trades),
            total_costs=total_costs,
            orders=order_values,
            fills=fill_values,
            trades=trade_values,
            equity_curve=curve_values,
            metrics=metrics,
            benchmark=benchmark_comparison(request.benchmark_symbol, period_benchmark),
            report=report,
        )

    @staticmethod
    def _validate_data(
        request: BacktestRequest,
        bars_by_symbol: dict[str, tuple[StrategyBar, ...]],
        benchmark_bars: tuple[StrategyBar, ...],
    ) -> None:
        if set(bars_by_symbol) != set(request.symbols):
            raise BacktestInvariantError("backtest data does not match requested symbols")
        for symbol, bars in (
            *bars_by_symbol.items(),
            (request.benchmark_symbol, benchmark_bars),
        ):
            identities = []
            for bar in bars:
                if bar.symbol != symbol:
                    raise BacktestInvariantError("backtest bar has the wrong symbol")
                if bar.provider_id != request.provider_id:
                    raise BacktestInvariantError("backtest cannot blend providers")
                if bar.interval is not BarInterval.DAY_1:
                    raise BacktestInvariantError(
                        "daily backtest cannot consume intraday bars"
                    )
                identities.append((bar.bar_end, bar.known_available_at))
            if identities != sorted(identities):
                raise BacktestInvariantError("backtest bars must be ordered")
            if len({bar.bar_start for bar in bars}) != len(bars):
                raise BacktestInvariantError("backtest bars must be unique")

    @staticmethod
    def _signal_at_clock(
        *,
        request: BacktestRequest,
        strategy: Strategy,
        parameters: BaseModel,
        symbol: str,
        clock_bar: StrategyBar,
        symbol_history: tuple[StrategyBar, ...],
        benchmark_history: tuple[StrategyBar, ...],
        history_limit: int,
        data_hash: str,
    ) -> StrategySignal:
        as_of = clock_bar.bar_end
        symbol_index = bisect_right(
            symbol_history, as_of, key=lambda bar: bar.bar_end
        )
        benchmark_index = bisect_right(
            benchmark_history, as_of, key=lambda bar: bar.bar_end
        )
        visible = tuple(
            bar
            for bar in symbol_history[
                max(0, symbol_index - history_limit) : symbol_index
            ]
            if bar.known_available_at <= as_of
        )
        visible_benchmark = tuple(
            bar
            for bar in benchmark_history[
                max(0, benchmark_index - history_limit) : benchmark_index
            ]
            if bar.known_available_at <= as_of
        )
        frame_hash = _hash(
            {
                "data_hash": data_hash,
                "symbol": symbol,
                "as_of": as_of.isoformat(),
                "candidate_count": len(visible),
                "benchmark_count": len(visible_benchmark),
                "candidate_through": (
                    visible[-1].bar_end.isoformat() if visible else None
                ),
                "benchmark_through": (
                    visible_benchmark[-1].bar_end.isoformat()
                    if visible_benchmark
                    else None
                ),
            }
        )
        frame = MarketFrame(
            symbol=symbol,
            benchmark_symbol=request.benchmark_symbol,
            provider_id=request.provider_id,
            as_of=as_of,
            input_hash=frame_hash,
            daily_bars=visible,
            benchmark_daily_bars=visible_benchmark,
        )
        return strategy.evaluate(frame, parameters)

    @staticmethod
    def _audit_signal(
        signal: StrategySignal,
        strategy: Strategy,
        clock_bar: StrategyBar,
    ) -> None:
        if signal.strategy_id != strategy.manifest.strategy_id:
            raise BacktestInvariantError("strategy returned the wrong identity")
        if signal.features.data_through and signal.features.data_through > clock_bar.bar_end:
            raise BacktestInvariantError("strategy output contains future feature data")
        if signal.entry and signal.entry.valid_after < clock_bar.bar_end:
            raise BacktestInvariantError("strategy entry intent predates its signal bar")

    def _process_entry(
        self,
        *,
        request: BacktestRequest,
        bar: StrategyBar,
        pending: _PendingEntry,
        cash: float,
        positions: dict[str, _Position],
        orders: dict[str, SimulatedOrder],
        fills: list[SimulatedFill],
        next_id,
    ) -> tuple[float, _Position | None]:
        order = orders[pending.order_id]
        if bar.open > pending.limit:
            orders[pending.order_id] = _completed_order(
                order,
                status=OrderStatus.REJECTED,
                completed_at=bar.bar_start,
                rejection_reason="opening_gap_exceeded_entry_zone",
            )
            return cash, None
        if bar.high < pending.trigger:
            orders[pending.order_id] = _completed_order(
                order,
                status=OrderStatus.REJECTED,
                completed_at=bar.bar_end,
                rejection_reason="entry_not_triggered_on_next_completed_bar",
            )
            return cash, None
        reference = max(bar.open, pending.trigger)
        if reference > pending.limit:
            orders[pending.order_id] = _completed_order(
                order,
                status=OrderStatus.REJECTED,
                completed_at=bar.bar_end,
                rejection_reason="trigger_exceeded_entry_zone",
            )
            return cash, None
        execution = reference * (1 + request.costs.slippage_bps / 10_000)
        opening_market_value = sum(
            position.quantity * position.entry_fill.execution_price
            for position in positions.values()
        )
        allocation = (cash + opening_market_value) * request.allocation_per_trade
        commission_rate = request.costs.commission_bps / 10_000
        affordable = cash / (execution * (1 + commission_rate))
        quantity = min(allocation / execution, affordable)
        if quantity <= 0:
            orders[pending.order_id] = _completed_order(
                order,
                status=OrderStatus.REJECTED,
                completed_at=bar.bar_start,
                rejection_reason="insufficient_cash",
            )
            return cash, None
        filled_at = bar.bar_start if bar.open >= pending.trigger else bar.bar_end
        fill = _fill(
            fill_id=next_id("entry_fill", bar.symbol, filled_at),
            order_id=pending.order_id,
            symbol=bar.symbol,
            side=OrderSide.BUY,
            filled_at=filled_at,
            reference_price=reference,
            execution_price=execution,
            quantity=quantity,
            commission_bps=request.costs.commission_bps,
        )
        fills.append(fill)
        orders[pending.order_id] = _filled_order(order, fill)
        cash -= fill.notional + fill.commission
        return max(cash, 0), _Position(
            symbol=bar.symbol,
            entry_order_id=pending.order_id,
            entry_fill=fill,
            entry_signal_at=pending.signal_at,
            quantity=quantity,
            initial_stop=pending.stop,
            active_stop=pending.stop,
            target=pending.target,
            trailing_atr_multiple=pending.trailing_atr_multiple,
            maximum_holding_sessions=pending.maximum_holding_sessions,
            exit_on_trend_failure=pending.exit_on_trend_failure,
            highest_price=max(bar.high, execution),
            holding_sessions=1,
            entry_regime=pending.regime,
        )

    def _close_position(
        self,
        *,
        request: BacktestRequest,
        position: _Position,
        reason: OrderReason,
        reference_price: float,
        filled_at,
        submitted_at,
        cash: float,
        orders: dict[str, SimulatedOrder],
        fills: list[SimulatedFill],
        trades: list[BacktestTrade],
        next_id,
    ) -> float:
        execution = reference_price * (1 - request.costs.slippage_bps / 10_000)
        order_id = next_id("exit_order", position.symbol, filled_at)
        fill_id = next_id("exit_fill", position.symbol, filled_at)
        fill = _fill(
            fill_id=fill_id,
            order_id=order_id,
            symbol=position.symbol,
            side=OrderSide.SELL,
            filled_at=filled_at,
            reference_price=reference_price,
            execution_price=execution,
            quantity=position.quantity,
            commission_bps=request.costs.commission_bps,
        )
        order = SimulatedOrder(
            order_id=order_id,
            symbol=position.symbol,
            side=OrderSide.SELL,
            reason=reason,
            status=OrderStatus.FILLED,
            submitted_at=submitted_at,
            eligible_after=submitted_at + timedelta(microseconds=1),
            signal_bar_end=(
                position.pending_exit_signal_at
                if reason in {OrderReason.TIME_EXIT, OrderReason.TREND_FAILURE}
                else None
            ),
            trigger_price=reference_price,
            requested_quantity=position.quantity,
            completed_at=filled_at,
            fill_id=fill_id,
        )
        orders[order_id] = order
        fills.append(fill)
        cash += fill.notional - fill.commission
        entry = position.entry_fill
        gross_pnl = (fill.execution_price - entry.execution_price) * position.quantity
        net_pnl = (fill.notional - fill.commission) - (
            entry.notional + entry.commission
        )
        trade_costs = (
            entry.commission
            + fill.commission
            + entry.slippage_cost
            + fill.slippage_cost
        )
        trade_id = next_id("trade", position.symbol, filled_at)
        trades.append(
            BacktestTrade(
                trade_id=trade_id,
                symbol=position.symbol,
                entry_order_id=position.entry_order_id,
                entry_fill_id=entry.fill_id,
                exit_order_id=order_id,
                exit_fill_id=fill_id,
                entry_signal_at=position.entry_signal_at,
                entered_at=entry.filled_at,
                exited_at=filled_at,
                entry_price=entry.execution_price,
                exit_price=fill.execution_price,
                quantity=position.quantity,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                return_pct=net_pnl / (entry.notional + entry.commission) * 100,
                costs=trade_costs,
                holding_sessions=position.holding_sessions,
                exit_reason=reason,
                entry_regime=position.entry_regime,
            )
        )
        return cash


def _exit_event(
    position: _Position,
    bar: StrategyBar,
) -> tuple[OrderReason | None, float, Any, Any]:
    stop_reason = (
        OrderReason.TRAILING_STOP
        if position.active_stop > position.initial_stop
        else OrderReason.PROTECTIVE_STOP
    )
    if bar.open <= position.active_stop:
        return stop_reason, bar.open, bar.bar_start, position.entry_fill.filled_at
    if bar.open >= position.target:
        return OrderReason.PROFIT_TARGET, bar.open, bar.bar_start, position.entry_fill.filled_at
    stop_touched = bar.low <= position.active_stop
    target_touched = bar.high >= position.target
    if stop_touched:
        return stop_reason, position.active_stop, bar.bar_end, position.entry_fill.filled_at
    if target_touched:
        return OrderReason.PROFIT_TARGET, position.target, bar.bar_end, position.entry_fill.filled_at
    if position.pending_exit_reason is not None:
        assert position.pending_exit_signal_at is not None
        return (
            position.pending_exit_reason,
            bar.open,
            bar.bar_start,
            position.pending_exit_signal_at,
        )
    return None, 0.0, bar.bar_end, bar.bar_start


def _fill(
    *,
    fill_id: str,
    order_id: str,
    symbol: str,
    side: OrderSide,
    filled_at,
    reference_price: float,
    execution_price: float,
    quantity: float,
    commission_bps: float,
) -> SimulatedFill:
    notional = execution_price * quantity
    return SimulatedFill(
        fill_id=fill_id,
        order_id=order_id,
        symbol=symbol,
        side=side,
        filled_at=filled_at,
        reference_price=reference_price,
        execution_price=execution_price,
        quantity=quantity,
        notional=notional,
        commission=notional * commission_bps / 10_000,
        slippage_cost=abs(execution_price - reference_price) * quantity,
    )


def _filled_order(order: SimulatedOrder, fill: SimulatedFill) -> SimulatedOrder:
    return SimulatedOrder.model_validate(
        {
            **order.model_dump(),
            "status": OrderStatus.FILLED,
            "requested_quantity": fill.quantity,
            "completed_at": fill.filled_at,
            "fill_id": fill.fill_id,
        }
    )


def _completed_order(
    order: SimulatedOrder,
    *,
    status: OrderStatus,
    completed_at,
    rejection_reason: str | None,
) -> SimulatedOrder:
    return SimulatedOrder.model_validate(
        {
            **order.model_dump(),
            "status": status,
            "completed_at": completed_at,
            "rejection_reason": rejection_reason,
        }
    )


def _data_hash(
    bars_by_symbol: dict[str, tuple[StrategyBar, ...]],
    benchmark_bars: tuple[StrategyBar, ...],
    request: BacktestRequest,
) -> str:
    return _hash(
        {
            "symbols": {
                symbol: [bar.model_dump(mode="json") for bar in bars]
                for symbol, bars in sorted(bars_by_symbol.items())
            },
            "benchmark": [bar.model_dump(mode="json") for bar in benchmark_bars],
            "period": request.period.model_dump(mode="json"),
            "provider_id": request.provider_id,
        }
    )


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
