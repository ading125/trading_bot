"""Explicit registry of trusted deterministic strategy hypotheses."""

from investing_bot.strategies.breakout import (
    BreakoutParameters,
    BreakoutStrategy,
)
from investing_bot.strategies.protocols import Strategy
from investing_bot.strategies.registry import (
    StrategyRegistry,
    StrategyRegistryError,
    build_default_strategy_registry,
)
from investing_bot.strategies.trend_pullback import (
    TrendPullbackParameters,
    TrendPullbackStrategy,
)

__all__ = [
    "BreakoutParameters",
    "BreakoutStrategy",
    "Strategy",
    "StrategyRegistry",
    "StrategyRegistryError",
    "TrendPullbackParameters",
    "TrendPullbackStrategy",
    "build_default_strategy_registry",
]
