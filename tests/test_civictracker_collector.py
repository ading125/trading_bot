from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from httpx import AsyncClient, MockTransport, Request, Response
import pytest

from investing_bot.db import Database, JobRunRepository, SocialPostRepository
from investing_bot.models import ProviderCapability
from investing_bot.providers import (
    CapabilitySelection,
    CredentialPresenceStore,
    ProviderConfiguration,
    ProviderManager,
    ProviderRegistry,
    ProviderTarget,
)
from investing_bot.providers.civictracker import CivicTrackerProvider, build_json_manifest
from investing_bot.services import CivicTrackerCollector


MEMBER = "3094abf7-4a95-4b8d-8c8d-af7d1c3747a1"
NOW = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


def raw(post_id: str, content: str) -> dict:
    return {
        "id": int(post_id), "platform": "Truth Social", "post_id": post_id,
        "content": content, "posted_at": "2026-08-19T14:30:00+00:00",
        "media_urls": [],
        "original_url": f"https://truthsocial.com/@example/posts/{post_id}",
        "has_media": False, "official_uuid": MEMBER, "is_deleted": False,
    }


@pytest.mark.anyio
async def test_collector_stops_at_known_boundary_and_captures_boundary_edit(
    tmp_path: Path,
) -> None:
    dataset = [raw("3", "newest"), raw("2", "middle"), raw("1", "oldest")]

    def handler(request: Request) -> Response:
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        page = dataset[offset : offset + limit]
        return Response(
            200,
            json={
                "posts": page, "total": len(dataset), "limit": limit,
                "offset": offset, "has_more": offset + limit < len(dataset),
            },
            request=request,
        )

    database = Database(tmp_path / "collector.duckdb")
    database.connect()
    database.migrate()
    async with AsyncClient(transport=MockTransport(handler)) as client:
        provider = CivicTrackerProvider(
            member_uuid=MEMBER, client=client, retries=0, now=lambda: NOW
        )
        registry = ProviderRegistry()
        registry.register(build_json_manifest(), lambda: provider)
        manager = ProviderManager(
            registry=registry,
            configuration=ProviderConfiguration(
                selections={
                    ProviderCapability.SOCIAL_POSTS: CapabilitySelection(
                        primary=ProviderTarget(provider_id="civictracker_json")
                    )
                }
            ),
            credentials=CredentialPresenceStore(),
        )
        collector = CivicTrackerCollector(
            provider_manager=manager,
            posts=SocialPostRepository(database),
            jobs=JobRunRepository(database),
            member_uuid=MEMBER,
            page_size=2,
            max_pages=5,
            now=lambda: NOW,
        )
        first = await collector.collect_once()
        second = await collector.collect_once()
        dataset[0] = raw("3", "newest, edited")
        third = await collector.collect_once()

    assert first.new_posts == 3 and first.pages_fetched == 2
    assert second.new_posts == 0 and second.boundary_hit and second.pages_fetched == 1
    assert third.edited_posts == 1 and third.boundary_hit and third.pages_fetched == 1
    assert database.fetchone("SELECT COUNT(*) FROM social_posts") == (3,)
    assert database.fetchone(
        "SELECT current_revision FROM social_posts WHERE post_id = '3'"
    ) == (2,)
    database.close()
