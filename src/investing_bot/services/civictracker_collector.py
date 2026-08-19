"""Boundary-aware CivicTracker collection and polite background polling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from uuid import uuid4

from investing_bot.db import (
    CollectionSummary,
    JobRunRepository,
    PostWriteKind,
    SocialPostRepository,
)
from investing_bot.models import ProviderCapability, SocialPostsRequest
from investing_bot.providers import ProviderManager
from investing_bot.providers.contracts import (
    ConnectionTestResult,
    ProviderCallError,
    ProviderHealthState,
)


logger = logging.getLogger(__name__)
JOB_TYPE = "civictracker_collection"


class CollectionBusyError(RuntimeError):
    pass


class CivicTrackerCollector:
    def __init__(
        self,
        *,
        provider_manager: ProviderManager,
        posts: SocialPostRepository,
        jobs: JobRunRepository,
        member_uuid: str,
        page_size: int,
        max_pages: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.provider_manager = provider_manager
        self.posts = posts
        self.jobs = jobs
        self.member_uuid = member_uuid
        self.page_size = page_size
        self.max_pages = max_pages
        self._now = now

    async def collect_once(self) -> CollectionSummary:
        owner = f"collector-{uuid4()}"
        if not self.jobs.acquire_lease(
            job_type=JOB_TYPE,
            owner=owner,
            lease_duration=timedelta(minutes=15),
        ):
            raise CollectionBusyError("CivicTracker collection is already running")
        run = self.jobs.create(
            job_type=JOB_TYPE,
            code_version="0.1.0",
            config_hash=self.provider_manager.configuration.configuration_hash,
        )
        self.jobs.start(run.run_id, owner=owner)
        try:
            pinned = await self.provider_manager.pin(
                ProviderCapability.SOCIAL_POSTS,
                run_id=run.run_id,
            )
            self._persist_social_health()
            checkpoint = self.posts.get_checkpoint(
                provider_id=pinned.provider_id,
                official_uuid=self.member_uuid,
            )
            old_boundary = (
                (checkpoint.boundary_platform, checkpoint.boundary_post_id)
                if checkpoint is not None and checkpoint.boundary_platform is not None
                else None
            )
            cursor: str | None = None
            new_boundary: tuple[str, str] | None = None
            pages = new_count = duplicate_count = edited_count = skipped = 0
            boundary_hit = False

            for _ in range(self.max_pages):
                result = await pinned.fetch_social_posts(
                    SocialPostsRequest(
                        member_id=self.member_uuid,
                        cursor=cursor,
                        page_size=self.page_size,
                    )
                )
                pages += 1
                if result.items and new_boundary is None:
                    first = result.items[0]
                    new_boundary = (first.platform, first.post_id)
                for post in result.items:
                    write_kind = self.posts.store(post, official_uuid=self.member_uuid)
                    if write_kind is PostWriteKind.NEW:
                        new_count += 1
                    elif write_kind is PostWriteKind.EDITED:
                        edited_count += 1
                    else:
                        duplicate_count += 1
                    if not post.content or post.is_deleted or post.is_media_only:
                        skipped += 1
                    if old_boundary == (post.platform, post.post_id):
                        boundary_hit = True
                        break
                if boundary_hit or result.page is None or not result.page.has_more:
                    cursor = result.page.next_cursor if result.page else None
                    break
                cursor = result.page.next_cursor

            completed_at = self._now()
            boundary = new_boundary or old_boundary
            self.posts.save_checkpoint(
                provider_id=pinned.provider_id,
                official_uuid=self.member_uuid,
                boundary=boundary,
                last_cursor=cursor,
                succeeded_at=completed_at,
            )
            summary = CollectionSummary(
                run_id=run.run_id,
                provider_id=pinned.provider_id,
                official_uuid=self.member_uuid,
                pages_fetched=pages,
                new_posts=new_count,
                duplicate_posts=duplicate_count,
                edited_posts=edited_count,
                skipped_discovery=skipped,
                boundary_hit=boundary_hit,
                recorded_at=completed_at,
            )
            self.posts.record_summary(summary)
            self.jobs.succeed(run.run_id, finished_at=completed_at)
            return summary
        except Exception as exc:
            if isinstance(exc, ProviderCallError):
                self.posts.record_health(
                    ConnectionTestResult(
                        provider_id=exc.failure.provider_id,
                        capability=ProviderCapability.SOCIAL_POSTS,
                        state=ProviderHealthState.UNHEALTHY,
                        checked_at=self._now(),
                        latency_ms=0,
                        error_code=exc.failure.code,
                        message=exc.failure.safe_message,
                    ),
                    next_poll_at=None,
                )
            else:
                self._persist_social_health()
            self.jobs.fail(run.run_id, error_summary=str(exc) or type(exc).__name__)
            raise
        finally:
            self.jobs.release_lease(job_type=JOB_TYPE, owner=owner)

    def _persist_social_health(self) -> None:
        for result in self.provider_manager.health_snapshot():
            if result.capability is ProviderCapability.SOCIAL_POSTS:
                self.posts.record_health(result, next_poll_at=None)


class CivicTrackerPollingService:
    def __init__(self, collector: CivicTrackerCollector, *, interval_seconds: int) -> None:
        self.collector = collector
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="civictracker-polling")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                summary = await self.collector.collect_once()
                logger.info(
                    "CivicTracker collection completed",
                    extra=summary.model_dump(mode="json"),
                )
            except CollectionBusyError:
                logger.info("CivicTracker collection skipped because a run is active")
            except Exception as exc:
                if isinstance(exc, ProviderCallError):
                    logger.error(
                        "CivicTracker collection failed",
                        extra={
                            "provider_id": exc.failure.provider_id,
                            "provider_error_code": exc.failure.code.value,
                            "safe_message": exc.failure.safe_message,
                            "retryable": exc.failure.retryable,
                        },
                        exc_info=True,
                    )
                else:
                    logger.exception("CivicTracker collection failed")
            await asyncio.sleep(self.interval_seconds)
