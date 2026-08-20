"""Incremental, validation-gated market-data collection services."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from investing_bot.db import JobRunRepository, MarketDataRepository, MarketWriteKind
from investing_bot.models import (
    BarInterval,
    BarsRequest,
    CorporateActionsRequest,
    EarningsRequest,
    NewsRequest,
    PriceAdjustment,
    ProviderCapability,
    QuotesRequest,
)
from investing_bot.providers import ProviderManager
from investing_bot.services.sp500 import SP500UniverseCollector


logger = logging.getLogger(__name__)


class MarketDataValidationError(RuntimeError):
    pass


class MarketCollectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    provider_id: str
    capability: ProviderCapability
    requested_symbols: int
    stored_records: int
    duplicate_records: int
    revised_records: int
    quarantined: bool
    recorded_at: datetime


class MarketDataValidator:
    def __init__(self, *, now=lambda: datetime.now(UTC)) -> None:
        self._now = now

    def validate_bars(self, request: BarsRequest, items: tuple) -> tuple[str, ...]:
        errors: list[str] = []
        requested = set(request.symbols)
        returned = {item.symbol for item in items}
        missing = requested - returned
        if missing:
            errors.append(f"missing requested symbols: {', '.join(sorted(missing))}")
        identities = [
            (item.symbol, item.interval, item.adjustment, item.bar_start)
            for item in items
        ]
        if len(set(identities)) != len(identities):
            errors.append("duplicate bar timestamps")
        by_symbol: dict[str, list] = defaultdict(list)
        for item in items:
            if item.symbol not in requested:
                errors.append(f"unexpected symbol: {item.symbol}")
            if item.interval is not request.interval or item.adjustment is not request.adjustment:
                errors.append("bar interval or adjustment does not match request")
            if not request.start <= item.bar_end <= request.end:
                errors.append("bar lies outside requested range")
            by_symbol[item.symbol].append(item)
        for symbol, bars in by_symbol.items():
            ordered = sorted(bars, key=lambda item: item.bar_start)
            for prior, current in zip(ordered, ordered[1:]):
                if prior.close and abs(current.close / prior.close - 1) > 0.80:
                    errors.append(f"unexplained extreme discontinuity for {symbol}")
                    break
        now = self._now()
        if request.end >= now - timedelta(days=1) and items:
            newest = max(item.bar_end for item in items)
            # A four-day intraday allowance covers weekends and ordinary US
            # market holidays without treating an overnight shutdown as stale.
            tolerance = (
                timedelta(days=5)
                if request.interval is BarInterval.DAY_1
                else timedelta(days=4)
            )
            if newest < now - tolerance:
                errors.append("latest completed bar is stale")
        return tuple(dict.fromkeys(errors))


class MarketDataCollector:
    def __init__(
        self,
        *,
        provider_manager: ProviderManager,
        repository: MarketDataRepository,
        jobs: JobRunRepository,
        validator: MarketDataValidator | None = None,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.provider_manager = provider_manager
        self.repository = repository
        self.jobs = jobs
        self.validator = validator or MarketDataValidator(now=now)
        self._now = now

    async def collect_bars(
        self,
        *,
        symbols: tuple[str, ...],
        start: datetime,
        end: datetime,
        interval: BarInterval,
        adjustment: PriceAdjustment,
    ) -> MarketCollectionSummary:
        capability = (
            ProviderCapability.DAILY_BARS
            if interval is BarInterval.DAY_1
            else ProviderCapability.INTRADAY_BARS
        )
        owner = f"market-{uuid4()}"
        job_type = f"market_{capability.value}"
        if not self.jobs.acquire_lease(
            job_type=job_type, owner=owner, lease_duration=timedelta(minutes=30)
        ):
            raise RuntimeError(f"{capability.value} collection is already running")
        run = self.jobs.create(
            job_type=job_type,
            code_version="0.1.0",
            config_hash=self.provider_manager.configuration.configuration_hash,
        )
        self.jobs.start(run.run_id, owner=owner, lease_duration=timedelta(minutes=30))
        provider_id = "provider_manager"
        stored = duplicates = revised = 0
        quarantined = False
        try:
            pinned = await self.provider_manager.pin(capability, run_id=run.run_id)
            provider_id = pinned.provider_id
            groups: dict[datetime, list[str]] = defaultdict(list)
            for symbol in symbols:
                latest = self.repository.latest_bar_end(
                    provider_id=provider_id,
                    symbol=symbol,
                    interval=interval,
                    adjustment=adjustment,
                )
                covered = self.repository.latest_covered_end(
                    provider_id=provider_id,
                    symbol=symbol,
                    interval=interval,
                    adjustment=adjustment,
                )
                candidates = [start]
                if latest is not None:
                    candidates.append(latest + timedelta(microseconds=1))
                if covered is not None:
                    candidates.append(covered)
                missing_start = max(candidates)
                if missing_start < end:
                    groups[missing_start].append(symbol)
            for missing_start, grouped_symbols in sorted(groups.items()):
                request = BarsRequest(
                    symbols=tuple(grouped_symbols),
                    start=missing_start,
                    end=end,
                    interval=interval,
                    adjustment=adjustment,
                )
                result = await pinned.fetch_bars(request)
                errors = self.validator.validate_bars(request, result.items)
                if errors:
                    quarantined = True
                    self.repository.quarantine(
                        run_id=run.run_id,
                        provider_id=provider_id,
                        capability=capability.value,
                        request_hash=_request_hash(request),
                        reason="; ".join(errors),
                        payload=result.model_dump(mode="json"),
                    )
                    raise MarketDataValidationError("; ".join(errors))
                counts = self.repository.store_bars(result.items)
                self.repository.mark_covered(
                    provider_id=provider_id,
                    symbols=tuple(grouped_symbols),
                    interval=interval,
                    adjustment=adjustment,
                    covered_through=end,
                )
                stored += counts[MarketWriteKind.NEW]
                duplicates += counts[MarketWriteKind.DUPLICATE]
                revised += counts[MarketWriteKind.REVISED]
            completed = self._now()
            summary = MarketCollectionSummary(
                run_id=run.run_id,
                provider_id=provider_id,
                capability=capability,
                requested_symbols=len(symbols),
                stored_records=stored,
                duplicate_records=duplicates,
                revised_records=revised,
                quarantined=quarantined,
                recorded_at=completed,
            )
            self.repository.record_collection_summary(summary)
            self.jobs.succeed(run.run_id, finished_at=completed)
            return summary
        except Exception as exc:
            self.jobs.fail(run.run_id, error_summary=str(exc) or type(exc).__name__)
            raise
        finally:
            self.jobs.release_lease(job_type=job_type, owner=owner)

    async def collect_events(self, *, symbols: tuple[str, ...]) -> dict[str, int]:
        run_id = str(uuid4())
        quotes = await (
            await self.provider_manager.pin(ProviderCapability.QUOTES, run_id=run_id)
        ).fetch_quotes(QuotesRequest(symbols=symbols))
        start = self._now() - timedelta(days=730)
        end = self._now() + timedelta(days=1)
        actions = await (
            await self.provider_manager.pin(
                ProviderCapability.CORPORATE_ACTIONS, run_id=run_id
            )
        ).fetch_corporate_actions(
            CorporateActionsRequest(symbols=symbols, start=start, end=end)
        )
        news = await (
            await self.provider_manager.pin(ProviderCapability.NEWS, run_id=run_id)
        ).fetch_news(NewsRequest(symbols=symbols, limit_per_symbol=20))
        earnings = await (
            await self.provider_manager.pin(ProviderCapability.EARNINGS, run_id=run_id)
        ).fetch_earnings(EarningsRequest(symbols=symbols))
        return {
            "quotes": self.repository.store_quotes(quotes.items),
            "corporate_actions": self.repository.store_actions(actions.items),
            "news": self.repository.store_news(news.items),
            "earnings": self.repository.store_earnings(earnings.items),
        }


class MarketPollingService:
    """Bounded opt-in polling for the configured research watchlist."""

    def __init__(
        self,
        collector: MarketDataCollector,
        *,
        symbols: tuple[str, ...],
        interval_seconds: int,
        daily_history_days: int,
        intraday_history_days: int,
        universe_collector: SP500UniverseCollector | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.collector = collector
        self.symbols = symbols
        self.interval_seconds = interval_seconds
        self.daily_history_days = daily_history_days
        self.intraday_history_days = intraday_history_days
        self.universe_collector = universe_collector
        self._universe_collected = False
        self._now = now
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="market-data-polling")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def collect_once(self) -> None:
        if self.universe_collector is not None and not self._universe_collected:
            try:
                snapshot = await self.universe_collector.collect()
                self._universe_collected = True
                logger.info(
                    "S&P 500 universe snapshot collected",
                    extra={
                        "snapshot_id": snapshot.snapshot_id,
                        "members": len(snapshot.members),
                    },
                )
            except Exception:
                logger.exception("S&P 500 universe collection failed")
        end = self._now()
        daily = await self.collector.collect_bars(
            symbols=self.symbols,
            start=end - timedelta(days=self.daily_history_days),
            end=end,
            interval=BarInterval.DAY_1,
            adjustment=PriceAdjustment.ADJUSTED,
        )
        intraday = await self.collector.collect_bars(
            symbols=self.symbols,
            start=end - timedelta(days=self.intraday_history_days),
            end=end,
            interval=BarInterval.MINUTE_15,
            adjustment=PriceAdjustment.ADJUSTED,
        )
        event_counts = await self.collector.collect_events(symbols=self.symbols)
        logger.info(
            "market collection completed",
            extra={
                "daily": daily.model_dump(mode="json"),
                "intraday": intraday.model_dump(mode="json"),
                "events": event_counts,
            },
        )

    async def _run(self) -> None:
        while True:
            try:
                await self.collect_once()
            except Exception:
                logger.exception("market collection failed")
            await asyncio.sleep(self.interval_seconds)


def _request_hash(request: BarsRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode()).hexdigest()
