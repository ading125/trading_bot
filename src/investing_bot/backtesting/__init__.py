"""Deterministic event-driven backtesting over trusted strategy plugins."""

from investing_bot.backtesting.engine import (
    BacktestEngine,
    BacktestInvariantError,
)

__all__ = ["BacktestEngine", "BacktestInvariantError"]
