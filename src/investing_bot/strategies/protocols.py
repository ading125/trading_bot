"""Narrow interface implemented only by packaged, reviewed strategies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from investing_bot.models import MarketFrame, StrategyManifest, StrategySignal


@runtime_checkable
class Strategy(Protocol):
    @property
    def manifest(self) -> StrategyManifest: ...

    def validate_parameters(
        self, values: Mapping[str, Any] | BaseModel | None = None
    ) -> BaseModel: ...

    def evaluate(
        self, frame: MarketFrame, parameters: BaseModel
    ) -> StrategySignal: ...
