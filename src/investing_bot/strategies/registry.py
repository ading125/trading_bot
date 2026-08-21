"""Source-controlled registry that cannot load strategies from writable data."""

from __future__ import annotations

from investing_bot.models import StrategyManifest
from investing_bot.strategies.breakout import BreakoutStrategy
from investing_bot.strategies.protocols import Strategy
from investing_bot.strategies.trend_pullback import TrendPullbackStrategy


class StrategyRegistryError(ValueError):
    pass


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        if not isinstance(strategy, Strategy):
            raise StrategyRegistryError("strategy does not implement the trusted contract")
        strategy_id = strategy.manifest.strategy_id
        if strategy_id in self._strategies:
            raise StrategyRegistryError(f"strategy {strategy_id} is already registered")
        self._strategies[strategy_id] = strategy

    def get(self, strategy_id: str) -> Strategy:
        try:
            return self._strategies[strategy_id]
        except KeyError as exc:
            raise StrategyRegistryError(f"strategy {strategy_id} is not registered") from exc

    def manifests(self) -> tuple[StrategyManifest, ...]:
        return tuple(
            self._strategies[strategy_id].manifest
            for strategy_id in sorted(self._strategies)
        )

    def strategies(self) -> tuple[Strategy, ...]:
        return tuple(
            self._strategies[strategy_id]
            for strategy_id in sorted(self._strategies)
        )


def build_default_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(TrendPullbackStrategy())
    registry.register(BreakoutStrategy())
    return registry
