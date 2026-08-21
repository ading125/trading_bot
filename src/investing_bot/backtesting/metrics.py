"""Transparent performance metrics for deterministic backtest results."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import sqrt
from statistics import mean, pstdev

from investing_bot.models import (
    BacktestCostModel,
    BacktestMetrics,
    BacktestTrade,
    BenchmarkComparison,
    EquityPoint,
    MarketRegime,
    RegimePerformance,
    StrategyBar,
    YearPerformance,
)


def calculate_metrics(
    *,
    starting_capital: float,
    equity_curve: tuple[EquityPoint, ...],
    trades: tuple[BacktestTrade, ...],
    total_costs: float,
    total_fill_notional: float,
    costs: BacktestCostModel,
) -> BacktestMetrics:
    ending_equity = equity_curve[-1].equity if equity_curve else starting_capital
    total_return = (ending_equity / starting_capital - 1) * 100
    years = _elapsed_years(equity_curve)
    cagr = (
        ((ending_equity / starting_capital) ** (1 / years) - 1) * 100
        if years > 0 and ending_equity > 0
        else None
    )
    returns = [trade.return_pct for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    win_rate = len(wins) / len(returns) if returns else None
    average_win = mean(wins) if wins else None
    average_loss = mean(losses) if losses else None
    payoff = (
        average_win / abs(average_loss)
        if average_win is not None and average_loss not in {None, 0}
        else None
    )
    expectancy = mean(returns) if returns else None
    gross_profit = sum(max(trade.net_pnl, 0) for trade in trades)
    gross_loss = abs(sum(min(trade.net_pnl, 0) for trade in trades))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    daily_returns = _equity_returns(equity_curve)
    annualized_volatility = (
        pstdev(daily_returns) * sqrt(252) * 100 if len(daily_returns) >= 2 else None
    )
    daily_risk_free = costs.risk_free_rate_annual / 252
    excess_returns = [value - daily_risk_free for value in daily_returns]
    sharpe = None
    if len(excess_returns) >= 2 and pstdev(excess_returns) > 0:
        sharpe = mean(excess_returns) / pstdev(excess_returns) * sqrt(252)
    average_equity = mean(point.equity for point in equity_curve) if equity_curve else starting_capital
    return BacktestMetrics(
        total_return_pct=_round(total_return),
        cagr_pct=_optional_round(cagr),
        win_rate=_optional_round(win_rate),
        average_win_pct=_optional_round(average_win),
        average_loss_pct=_optional_round(average_loss),
        payoff_ratio=_optional_round(payoff),
        expectancy_pct=_optional_round(expectancy),
        profit_factor=_optional_round(profit_factor),
        maximum_drawdown_pct=_round(
            min((point.drawdown_pct for point in equity_curve), default=0)
        ),
        recovery_sessions=_recovery_sessions(equity_curve),
        annualized_volatility_pct=_optional_round(annualized_volatility),
        sharpe_ratio=_optional_round(sharpe),
        exposure_pct=_round(
            100
            * sum(point.exposed for point in equity_curve)
            / max(len(equity_curve), 1)
        ),
        turnover=_round(total_fill_notional / max(average_equity, 1e-12)),
        trade_count=len(trades),
        average_holding_sessions=(
            _round(mean(trade.holding_sessions for trade in trades))
            if trades
            else None
        ),
        total_costs=_round(total_costs),
        regimes=_regime_performance(trades),
        years=_year_performance(trades, starting_capital),
    )


def benchmark_comparison(
    symbol: str,
    bars: tuple[StrategyBar, ...],
) -> BenchmarkComparison:
    if not bars:
        raise ValueError("benchmark comparison requires bars")
    closes = [bar.close for bar in bars]
    total_return = (closes[-1] / closes[0] - 1) * 100
    elapsed = max((bars[-1].session_date - bars[0].session_date).days / 365.25, 0)
    cagr = (
        ((closes[-1] / closes[0]) ** (1 / elapsed) - 1) * 100
        if elapsed > 0
        else None
    )
    peak = closes[0]
    max_drawdown = 0.0
    for close in closes:
        peak = max(peak, close)
        max_drawdown = min(max_drawdown, (close / peak - 1) * 100)
    return BenchmarkComparison(
        symbol=symbol,
        start_close=closes[0],
        end_close=closes[-1],
        total_return_pct=_round(total_return),
        cagr_pct=_optional_round(cagr),
        maximum_drawdown_pct=_round(max_drawdown),
    )


def classify_regimes(
    bars: tuple[StrategyBar, ...],
) -> dict[date, MarketRegime]:
    regimes: dict[date, MarketRegime] = {}
    closes: list[float] = []
    for bar in bars:
        closes.append(bar.close)
        if len(closes) < 50:
            regimes[bar.session_date] = MarketRegime.UNKNOWN
            continue
        recent = closes[-20:]
        daily = [recent[index] / recent[index - 1] - 1 for index in range(1, 20)]
        annualized_volatility = pstdev(daily) * sqrt(252) if len(daily) >= 2 else 0
        period_return = recent[-1] / recent[0] - 1
        average_50 = mean(closes[-50:])
        if annualized_volatility >= 0.30:
            regime = MarketRegime.HIGH_VOLATILITY
        elif abs(period_return) <= 0.03:
            regime = MarketRegime.SIDEWAYS
        elif period_return > 0 and bar.close >= average_50:
            regime = MarketRegime.BULLISH
        else:
            regime = MarketRegime.BEARISH
        regimes[bar.session_date] = regime
    return regimes


def _equity_returns(equity_curve: tuple[EquityPoint, ...]) -> list[float]:
    returns = []
    for previous, current in zip(equity_curve, equity_curve[1:]):
        if previous.equity > 0:
            returns.append(current.equity / previous.equity - 1)
    return returns


def _elapsed_years(equity_curve: tuple[EquityPoint, ...]) -> float:
    if len(equity_curve) < 2:
        return 0
    days = (equity_curve[-1].session_date - equity_curve[0].session_date).days
    return max(days / 365.25, 0)


def _recovery_sessions(equity_curve: tuple[EquityPoint, ...]) -> int | None:
    if not equity_curve:
        return None
    equities = [point.equity for point in equity_curve]
    running_peak = equities[0]
    peak_index = 0
    worst_index = 0
    worst_drawdown = 0.0
    worst_peak_index = 0
    for index, equity in enumerate(equities):
        if equity > running_peak:
            running_peak = equity
            peak_index = index
        drawdown = equity / running_peak - 1 if running_peak else 0
        if drawdown < worst_drawdown:
            worst_drawdown = drawdown
            worst_index = index
            worst_peak_index = peak_index
    if worst_drawdown == 0:
        return 0
    peak_value = equities[worst_peak_index]
    for index in range(worst_index + 1, len(equities)):
        if equities[index] >= peak_value:
            return index - worst_index
    return None


def _regime_performance(
    trades: tuple[BacktestTrade, ...],
) -> tuple[RegimePerformance, ...]:
    grouped: dict[MarketRegime, list[BacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.entry_regime].append(trade)
    return tuple(
        RegimePerformance(
            regime=regime,
            trade_count=len(items),
            win_rate=(sum(item.net_pnl > 0 for item in items) / len(items)),
            net_pnl=_round(sum(item.net_pnl for item in items)),
            expectancy_pct=_round(mean(item.return_pct for item in items)),
        )
        for regime, items in sorted(grouped.items(), key=lambda item: item[0].value)
    )


def _year_performance(
    trades: tuple[BacktestTrade, ...], starting_capital: float
) -> tuple[YearPerformance, ...]:
    grouped: dict[int, list[BacktestTrade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.exited_at.year].append(trade)
    return tuple(
        YearPerformance(
            year=year,
            trade_count=len(items),
            net_pnl=_round(sum(item.net_pnl for item in items)),
            return_pct=_round(
                sum(item.net_pnl for item in items) / starting_capital * 100
            ),
        )
        for year, items in sorted(grouped.items())
    )


def _round(value: float) -> float:
    return round(float(value), 8)


def _optional_round(value: float | None) -> float | None:
    return None if value is None else _round(value)
