"""Small deterministic indicators calculated only from a supplied market frame."""

from __future__ import annotations

from collections.abc import Sequence

from investing_bot.models import MarketFrame, StrategyBar, StrategyFeatures


def calculate_features(
    frame: MarketFrame,
    *,
    fast_period: int,
    slow_period: int,
    atr_period: int,
    volume_period: int,
    relative_strength_lookback: int,
    breakout_period: int,
) -> StrategyFeatures:
    bars = frame.daily_bars
    close = bars[-1].close if bars else None
    fast = moving_average([bar.close for bar in bars], fast_period)
    slow = moving_average([bar.close for bar in bars], slow_period)
    atr = average_true_range(bars, atr_period)
    prior_volumes = [bar.volume for bar in bars[:-1]]
    average_volume = moving_average(prior_volumes, volume_period)
    volume_ratio = None
    if bars and average_volume is not None and average_volume > 0:
        volume_ratio = bars[-1].volume / average_volume
    relative_strength = relative_strength_pct(
        bars, frame.benchmark_daily_bars, relative_strength_lookback
    )
    breakout_level = None
    if len(bars) >= breakout_period + 1:
        breakout_level = max(bar.high for bar in bars[-(breakout_period + 1) : -1])
    distance = None
    if close is not None and fast is not None and atr is not None:
        distance = (close - fast) / atr
    intraday_confirmed = None
    if len(frame.intraday_bars) >= 2:
        prior, current = frame.intraday_bars[-2:]
        intraday_confirmed = (
            current.close > prior.high and current.close > current.open
        )
    return StrategyFeatures(
        daily_bar_count=len(bars),
        benchmark_bar_count=len(frame.benchmark_daily_bars),
        intraday_bar_count=len(frame.intraday_bars),
        data_through=bars[-1].bar_end if bars else None,
        close=_rounded(close),
        fast_moving_average=_rounded(fast),
        slow_moving_average=_rounded(slow),
        atr=_rounded(atr),
        average_volume=_rounded(average_volume),
        relative_strength_pct=_rounded(relative_strength),
        distance_to_fast_atr=_rounded(distance),
        breakout_level=_rounded(breakout_level),
        volume_ratio=_rounded(volume_ratio),
        intraday_confirmed=intraday_confirmed,
    )


def moving_average(values: Sequence[float | int], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(float(value) for value in values[-period:]) / period


def average_true_range(
    bars: Sequence[StrategyBar], period: int
) -> float | None:
    if period <= 0 or len(bars) < period + 1:
        return None
    relevant = bars[-(period + 1) :]
    ranges = []
    for previous, current in zip(relevant, relevant[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(ranges) / period


def relative_strength_pct(
    bars: Sequence[StrategyBar],
    benchmark: Sequence[StrategyBar],
    lookback: int,
) -> float | None:
    candidate_by_date = {bar.session_date: bar.close for bar in bars}
    benchmark_by_date = {bar.session_date: bar.close for bar in benchmark}
    common_dates = sorted(candidate_by_date.keys() & benchmark_by_date.keys())
    if lookback <= 0 or len(common_dates) < lookback + 1:
        return None
    start = common_dates[-(lookback + 1)]
    end = common_dates[-1]
    candidate_return = candidate_by_date[end] / candidate_by_date[start] - 1
    benchmark_return = benchmark_by_date[end] / benchmark_by_date[start] - 1
    return (candidate_return - benchmark_return) * 100


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)
