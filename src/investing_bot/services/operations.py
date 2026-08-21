"""Market-day scheduling, guarded controls, and prospective outcome tracking."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
import inspect
import logging
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from investing_bot.db import (
    AnalysisRepository,
    JobRunRepository,
    JobStatus,
    MarketDataRepository,
    OperationsRepository,
)
from investing_bot.models import (
    ManualAction,
    ManualActionResult,
    OperationalTask,
    OutcomeReconciliationSummary,
    ScheduledOperation,
)


logger = logging.getLogger(__name__)
NEW_YORK = ZoneInfo("America/New_York")


class ManualActionBusyError(RuntimeError):
    pass


class ManualActionRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__(
            f"refresh was run recently; retry in {self.retry_after_seconds} seconds"
        )


class USMarketCalendar:
    """Small deterministic NYSE calendar without a network dependency.

    Regular US exchange holidays and the common 1 p.m. early closes are
    represented. Extraordinary closures remain an explicit operational
    override rather than being guessed by the application.
    """

    def is_session(self, value: date) -> bool:
        return value.weekday() < 5 and value not in self.holidays(value.year)

    def holidays(self, year: int) -> frozenset[date]:
        holidays = {
            _observed(date(year, 1, 1)),
            _observed(date(year + 1, 1, 1)),
            _nth_weekday(year, 2, 0, 3),
            _easter_sunday(year) - timedelta(days=2),
            _last_weekday(year, 5, 0),
            _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 11, 3, 4),
            _observed(date(year, 12, 25)),
        }
        if year >= 1998:
            holidays.add(_nth_weekday(year, 1, 0, 3))
        if year >= 2022:
            holidays.add(_observed(date(year, 6, 19)))
        return frozenset(day for day in holidays if day.year == year)

    def session_open(self, value: date) -> datetime:
        if not self.is_session(value):
            raise ValueError("date is not a market session")
        return datetime.combine(value, time(9, 30), NEW_YORK)

    def session_close(self, value: date) -> datetime:
        if not self.is_session(value):
            raise ValueError("date is not a market session")
        closing = time(13) if self.is_early_close(value) else time(16)
        return datetime.combine(value, closing, NEW_YORK)

    def is_early_close(self, value: date) -> bool:
        thanksgiving = _nth_weekday(value.year, 11, 3, 4)
        return value == thanksgiving + timedelta(days=1) or (
            value.month == 12 and value.day == 24 and self.is_session(value)
        ) or (
            value.month == 7 and value.day == 3 and self.is_session(value)
        )

    def is_market_open(self, value: datetime) -> bool:
        local = _aware(value).astimezone(NEW_YORK)
        return self.is_session(local.date()) and (
            self.session_open(local.date()) <= local < self.session_close(local.date())
        )

    def next_session(self, value: date, *, include_current: bool = False) -> date:
        candidate = value if include_current else value + timedelta(days=1)
        for _ in range(370):
            if self.is_session(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise RuntimeError("could not locate the next market session")


_DESCRIPTIONS = {
    OperationalTask.CIVICTRACKER: "Collect new CivicTracker source records",
    OperationalTask.BROAD_NEWS: "Refresh broad-market news",
    OperationalTask.ACTIVE_NEWS: "Refresh news for active candidates",
    OperationalTask.EARNINGS: "Refresh earnings events and results",
    OperationalTask.DAILY_PRICES: "Reconcile completed daily price bars",
    OperationalTask.INTRADAY_PRICES: "Collect the latest completed 15-minute bars",
    OperationalTask.AFTER_CLOSE_REPORT: "Rebuild candidates, analysis, and setup reports",
    OperationalTask.PROSPECTIVE_OUTCOMES: "Record due 5/10/20-session AI outcomes",
}


class OperationsSchedulePlanner:
    def __init__(self, calendar: USMarketCalendar | None = None) -> None:
        self.calendar = calendar or USMarketCalendar()

    def next_runs(self, now: datetime | None = None) -> tuple[ScheduledOperation, ...]:
        current = _aware(now or datetime.now(UTC)).astimezone(NEW_YORK)
        operations = [self._next(task, current) for task in OperationalTask]
        return tuple(sorted(operations, key=lambda item: item.scheduled_for))

    def due_between(
        self, start: datetime, end: datetime
    ) -> tuple[ScheduledOperation, ...]:
        beginning = _aware(start).astimezone(NEW_YORK)
        ending = _aware(end).astimezone(NEW_YORK)
        if ending <= beginning:
            return ()
        results: list[ScheduledOperation] = []
        day = beginning.date()
        while day <= ending.date():
            for task in OperationalTask:
                for scheduled in self._occurrences(task, day):
                    if beginning < scheduled <= ending:
                        results.append(self._operation(task, scheduled))
            day += timedelta(days=1)
        return tuple(sorted(results, key=lambda item: (item.scheduled_for, item.task)))

    def _next(self, task: OperationalTask, after: datetime) -> ScheduledOperation:
        day = after.date()
        for _ in range(370):
            for scheduled in self._occurrences(task, day):
                if scheduled > after:
                    return self._operation(task, scheduled)
            day += timedelta(days=1)
        raise RuntimeError(f"could not schedule {task.value}")

    def _operation(
        self, task: OperationalTask, scheduled: datetime
    ) -> ScheduledOperation:
        market_only = task is not OperationalTask.CIVICTRACKER
        return ScheduledOperation(
            task=task,
            description=_DESCRIPTIONS[task],
            scheduled_for=scheduled,
            market_session_date=scheduled.date() if market_only else None,
            market_session_only=market_only,
        )

    def _occurrences(self, task: OperationalTask, day: date) -> tuple[datetime, ...]:
        if task is OperationalTask.CIVICTRACKER:
            return tuple(
                datetime.combine(day, time(hour, minute), NEW_YORK)
                for hour in range(24)
                for minute in (0, 15, 30, 45)
            )
        if not self.calendar.is_session(day):
            return ()
        close = self.calendar.session_close(day).time()
        if task is OperationalTask.BROAD_NEWS:
            times = _time_range(time(7, 30), close, 60)
        elif task is OperationalTask.ACTIVE_NEWS:
            times = _time_range(time(9, 30), close, 15)
        elif task is OperationalTask.EARNINGS:
            times = (time(7), _after(close, 15))
        elif task is OperationalTask.DAILY_PRICES:
            times = (_after(close, 10),)
        elif task is OperationalTask.INTRADAY_PRICES:
            times = _time_range(time(9, 45), close, 15)
        elif task is OperationalTask.AFTER_CLOSE_REPORT:
            times = (_after(close, 30),)
        else:
            times = (_after(close, 45),)
        return tuple(datetime.combine(day, item, NEW_YORK) for item in times)


class OutcomeTrackingService:
    def __init__(
        self,
        *,
        analyses: AnalysisRepository,
        market: MarketDataRepository,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.analyses = analyses
        self.market = market
        self._now = now

    def reconcile(self, *, as_of: datetime | None = None) -> OutcomeReconciliationSummary:
        timestamp = _aware(as_of or self._now()).astimezone(UTC)
        pending = self.analyses.pending_outcomes()
        assessments = {item.assessment_id for item in pending}
        baselines = outcomes = still_pending = 0
        for item in pending:
            anchor = item.assessment_created_at
            if item.baseline_session_date is not None:
                anchor = datetime.combine(
                    item.baseline_session_date - timedelta(days=1),
                    time.max,
                    UTC,
                )
            closes = self.market.outcome_closes(
                symbol=item.ticker,
                after=anchor,
                as_of=timestamp,
                provider_id=item.market_provider_id,
                limit=item.horizon_sessions + 10,
            )
            if not closes:
                still_pending += 1
                continue
            baseline_date = item.baseline_session_date or closes[0]["session_date"]
            baseline_close = item.baseline_close or float(closes[0]["close"])
            provider_id = item.market_provider_id or str(closes[0]["provider_id"])
            if item.baseline_session_date is None:
                self.analyses.record_outcome_baseline(
                    assessment_id=item.assessment_id,
                    horizon_sessions=item.horizon_sessions,
                    session_date=baseline_date,
                    close=baseline_close,
                    market_provider_id=provider_id,
                )
                baselines += 1
            eligible = [row for row in closes if row["session_date"] >= baseline_date]
            if len(eligible) <= item.horizon_sessions:
                still_pending += 1
                continue
            result = eligible[item.horizon_sessions]
            outcome_close = float(result["close"])
            return_pct = ((outcome_close / baseline_close) - 1.0) * 100.0
            self.analyses.record_outcome_result(
                assessment_id=item.assessment_id,
                horizon_sessions=item.horizon_sessions,
                session_date=result["session_date"],
                close=outcome_close,
                return_pct=return_pct,
                recorded_at=timestamp,
            )
            outcomes += 1
        return OutcomeReconciliationSummary(
            assessments_scanned=len(assessments),
            baselines_recorded=baselines,
            outcomes_recorded=outcomes,
            still_pending=still_pending,
            completed_at=timestamp,
        )


class OperationsCoordinator:
    """Bounded one-shot operations shared by the scheduler and dashboard."""

    def __init__(
        self,
        *,
        jobs: JobRunRepository,
        config_hash: str,
        source_collector: Any,
        market_poller: Any,
        candidate_service: Any,
        analysis_service: Any,
        strategy_service: Any,
        outcome_service: OutcomeTrackingService,
        analysis_symbols: tuple[str, ...],
        strategy_symbols: tuple[str, ...],
        scheduled_actions: frozenset[ManualAction] | None = None,
        cooldown: timedelta = timedelta(seconds=30),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.jobs = jobs
        self.config_hash = config_hash
        self.source_collector = source_collector
        self.market_poller = market_poller
        self.candidate_service = candidate_service
        self.analysis_service = analysis_service
        self.strategy_service = strategy_service
        self.outcome_service = outcome_service
        self.analysis_symbols = analysis_symbols
        self.strategy_symbols = strategy_symbols
        self.scheduled_actions = (
            frozenset(ManualAction)
            if scheduled_actions is None
            else scheduled_actions
        )
        self.cooldown = cooldown
        self._now = now
        self._locks = {action: asyncio.Lock() for action in ManualAction}
        self._scheduled_market_lock = asyncio.Lock()
        self._last_scheduled_market_refresh: datetime | None = None

    async def run_manual(self, action: ManualAction) -> ManualActionResult:
        job_type = f"manual_refresh:{action.value}"
        now = _aware(self._now()).astimezone(UTC)
        latest = self.jobs.latest_for_type(job_type)
        if (
            latest is not None
            and latest.status is JobStatus.SUCCEEDED
            and latest.finished_at is not None
            and now - latest.finished_at < self.cooldown
        ):
            remaining = self.cooldown - (now - latest.finished_at)
            raise ManualActionRateLimitError(int(remaining.total_seconds()) + 1)
        lock = self._locks[action]
        if lock.locked():
            raise ManualActionBusyError(f"{action.value} refresh is already running")
        owner = f"manual-{action.value}-{uuid4()}"
        if not self.jobs.acquire_lease(
            job_type=job_type,
            owner=owner,
            lease_duration=timedelta(minutes=30),
        ):
            raise ManualActionBusyError(f"{action.value} refresh is already running")
        run = self.jobs.create(
            job_type=job_type,
            code_version="0.1.0",
            config_hash=self.config_hash,
        )
        self.jobs.start(run.run_id, owner=owner, lease_duration=timedelta(minutes=30))
        try:
            async with lock:
                summary = await self._execute(action)
            completed = _aware(self._now()).astimezone(UTC)
            self.jobs.succeed(run.run_id, finished_at=completed)
            return ManualActionResult(
                action=action,
                run_id=run.run_id,
                summary=summary,
                completed_at=completed,
            )
        except Exception as exc:
            self.jobs.fail(run.run_id, error_summary=str(exc) or type(exc).__name__)
            raise
        finally:
            self.jobs.release_lease(job_type=job_type, owner=owner)

    async def run_scheduled(self, task: OperationalTask) -> str:
        if task is OperationalTask.CIVICTRACKER:
            if ManualAction.SOURCES not in self.scheduled_actions:
                return "source schedule is disabled"
            return await self._execute(ManualAction.SOURCES)
        if task in {
            OperationalTask.BROAD_NEWS,
            OperationalTask.ACTIVE_NEWS,
            OperationalTask.EARNINGS,
            OperationalTask.DAILY_PRICES,
            OperationalTask.INTRADAY_PRICES,
        }:
            if ManualAction.MARKET not in self.scheduled_actions:
                return "market schedule is disabled"
            async with self._scheduled_market_lock:
                now = _aware(self._now()).astimezone(UTC)
                if (
                    self._last_scheduled_market_refresh is not None
                    and now - self._last_scheduled_market_refresh
                    < timedelta(minutes=5)
                ):
                    return "market refresh coalesced with a related scheduled task"
                result = await self._execute(ManualAction.MARKET)
                self._last_scheduled_market_refresh = now
                return result
        if task is OperationalTask.AFTER_CLOSE_REPORT:
            parts = []
            for action in (
                ManualAction.CANDIDATES,
                ManualAction.ANALYSIS,
                ManualAction.STRATEGIES,
            ):
                if action in self.scheduled_actions:
                    parts.append(await self._execute(action))
            if not parts:
                return "after-close report schedule is disabled"
            return "; ".join(parts)
        return await self._execute(ManualAction.OUTCOMES)

    async def _execute(self, action: ManualAction) -> str:
        if action is ManualAction.SOURCES:
            result = await self.source_collector.collect_once()
            stored = result.new_posts + result.edited_posts
            return f"source collection stored {stored} records"
        if action is ManualAction.MARKET:
            await self.market_poller.collect_once()
            return "market collection completed"
        if action is ManualAction.CANDIDATES:
            result = await self.candidate_service.refresh()
            return f"candidate queue has {result.candidates_active} active symbols"
        if action is ManualAction.ANALYSIS:
            cached = completed = 0
            for symbol in self.analysis_symbols:
                result = await self.analysis_service.analyze(symbol)
                completed += 1
                cached += int(result.cached)
            return f"analysis completed for {completed} symbols ({cached} cached)"
        if action is ManualAction.STRATEGIES:
            evaluations = 0
            for symbol in self.strategy_symbols:
                result = self.strategy_service.evaluate_symbol(symbol)
                evaluations += len(result.evaluations)
            return f"strategy refresh produced {evaluations} evaluations"
        if action is ManualAction.OUTCOMES:
            result = self.outcome_service.reconcile()
            return f"recorded {result.outcomes_recorded} prospective outcomes"
        parts = []
        for child in (
            ManualAction.SOURCES,
            ManualAction.MARKET,
            ManualAction.CANDIDATES,
            ManualAction.ANALYSIS,
            ManualAction.STRATEGIES,
            ManualAction.OUTCOMES,
        ):
            parts.append(await self._execute(child))
        return "; ".join(parts)


class OperationalScheduler:
    def __init__(
        self,
        *,
        planner: OperationsSchedulePlanner,
        repository: OperationsRepository,
        handler: Callable[[OperationalTask], Awaitable[Any]],
        interval_seconds: float = 30,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.planner = planner
        self.repository = repository
        self.handler = handler
        self.interval_seconds = interval_seconds
        self._now = now
        self._task: asyncio.Task[None] | None = None
        self._last_check: datetime | None = None

    def start(self) -> None:
        if self._task is None:
            self._last_check = _aware(self._now()) - timedelta(minutes=1)
            self._task = asyncio.create_task(self._run(), name="operational-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_due(self, now: datetime | None = None) -> int:
        timestamp = _aware(now or self._now())
        start = self._last_check or timestamp - timedelta(minutes=1)
        self._last_check = timestamp
        return await self.run_window(start, timestamp)

    async def run_window(self, start: datetime, end: datetime) -> int:
        """Dispatch a bounded window; useful for deterministic market-day replay."""

        timestamp = _aware(end)
        completed = 0
        for operation in self.planner.due_between(_aware(start), timestamp):
            if not self.repository.claim(
                operation.task, operation.scheduled_for, started_at=timestamp
            ):
                continue
            try:
                result = self.handler(operation.task)
                if inspect.isawaitable(result):
                    await result
                self.repository.finish(operation.task, operation.scheduled_for)
                completed += 1
            except Exception as exc:
                self.repository.finish(
                    operation.task,
                    operation.scheduled_for,
                    error_summary=str(exc) or type(exc).__name__,
                )
                logger.exception(
                    "scheduled operation failed",
                    extra={"operational_task": operation.task.value},
                )
        return completed

    async def _run(self) -> None:
        while True:
            await self.run_due()
            await asyncio.sleep(self.interval_seconds)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


def _observed(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _nth_weekday(year: int, month: int, weekday: int, ordinal: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (ordinal - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    value = first_next - timedelta(days=1)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    longitude = (32 + 2 * e + 2 * i - h - k) % 7
    month_adjustment = (a + 11 * h + 22 * longitude) // 451
    month = (h + longitude - 7 * month_adjustment + 114) // 31
    day = (h + longitude - 7 * month_adjustment + 114) % 31 + 1
    return date(year, month, day)


def _time_range(start: time, end: time, minutes: int) -> tuple[time, ...]:
    anchor = datetime.combine(date(2000, 1, 1), start)
    finish = datetime.combine(date(2000, 1, 1), end)
    values: list[time] = []
    while anchor <= finish:
        values.append(anchor.time())
        anchor += timedelta(minutes=minutes)
    return tuple(values)


def _after(value: time, minutes: int) -> time:
    anchor = datetime.combine(date(2000, 1, 1), value) + timedelta(minutes=minutes)
    return anchor.time()
