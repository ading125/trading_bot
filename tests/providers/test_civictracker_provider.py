from __future__ import annotations

from datetime import UTC, datetime
import json

from httpx import AsyncClient, MockTransport, Request, Response
import pytest

from investing_bot.models import SocialPostsRequest
from investing_bot.providers.civictracker import (
    CivicTrackerProvider,
    parse_social_post_html,
)


MEMBER = "3094abf7-4a95-4b8d-8c8d-af7d1c3747a1"
NOW = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


def post_payload(*, content: str = "Jobs &amp; growth", post_id: str = "123") -> dict:
    return {
        "id": 99,
        "platform": "Truth Social",
        "post_id": post_id,
        "content": content,
        "posted_at": "2026-08-19T14:30:00+00:00",
        "media_urls": [],
        "original_url": f"https://truthsocial.com/@example/posts/{post_id}",
        "has_media": False,
        "official_uuid": MEMBER,
        "is_deleted": False,
    }


@pytest.mark.anyio
async def test_json_provider_retries_and_sends_polite_identity() -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if len(requests) == 1:
            return Response(503, request=request)
        payload = {
            "posts": [post_payload()],
            "total": 2,
            "limit": 1,
            "offset": 0,
            "has_more": True,
        }
        return Response(200, json=payload, request=request)

    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async with AsyncClient(transport=MockTransport(handler)) as client:
        provider = CivicTrackerProvider(
            member_uuid=MEMBER,
            client=client,
            sleep=sleep,
            jitter=lambda: 0,
            now=lambda: NOW,
        )
        result = await provider.fetch_social_posts(
            SocialPostsRequest(member_id=MEMBER, page_size=1)
        )

    assert sleeps == [0.5]
    assert requests[-1].headers["user-agent"].startswith("InvestingBot/")
    assert requests[-1].headers["referer"].startswith("https://civictracker.us/")
    assert requests[-1].url.params["official_uuid"] == MEMBER
    assert result.page is not None and result.page.next_cursor == "1"
    assert result.items[0].content == "Jobs & growth"
    assert result.items[0].post_id == "123"


@pytest.mark.anyio
async def test_json_and_html_normalize_to_same_semantic_schema() -> None:
    payload = {
        "posts": [post_payload()],
        "total": 1,
        "limit": 20,
        "offset": 0,
        "has_more": False,
    }

    def handler(request: Request) -> Response:
        return Response(200, json=payload, request=request)

    async with AsyncClient(transport=MockTransport(handler)) as client:
        json_record = (
            await CivicTrackerProvider(
                member_uuid=MEMBER, client=client, now=lambda: NOW
            ).fetch_social_posts(SocialPostsRequest(member_id=MEMBER))
        ).items[0]

    html = """
    <article class="social-post" data-post-id="123" data-platform="Truth Social">
      <div class="post-content">Jobs &amp;amp; growth</div>
      <time datetime="2026-08-19T14:30:00+00:00"></time>
      <a class="post-original-link"
         href="https://truthsocial.com/@example/posts/123">Original</a>
    </article>
    """
    html_record = parse_social_post_html(html, retrieved_at=NOW)[0]

    fields = (
        "platform", "post_id", "content", "original_url", "published_at",
        "is_media_only", "is_deleted", "content_hash",
    )
    assert {field: getattr(json_record, field) for field in fields} == {
        field: getattr(html_record, field) for field in fields
    }
    assert json.loads(json_record.model_dump_json())["metadata"]["schema_version"] == (
        "social_post.v1"
    )


@pytest.mark.anyio
async def test_anonymous_offset_restriction_becomes_a_clean_final_page() -> None:
    def handler(request: Request) -> Response:
        offset = int(request.url.params["offset"])
        if offset > 0:
            return Response(
                400,
                json={
                    "code": "invalid_argument_value",
                    "message": 'Invalid value for argument "offset". Allowed values: 0',
                    "data": {"status": 400},
                },
                request=request,
            )
        return Response(
            200,
            json={
                "posts": [post_payload()],
                "total": 30_637,
                "limit": 1,
                "offset": 0,
                "has_more": True,
            },
            request=request,
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        provider = CivicTrackerProvider(member_uuid=MEMBER, client=client, now=lambda: NOW)
        first = await provider.fetch_social_posts(
            SocialPostsRequest(member_id=MEMBER, page_size=1)
        )
        final = await provider.fetch_social_posts(
            SocialPostsRequest(member_id=MEMBER, page_size=1, cursor="1")
        )

    assert first.page is not None and first.page.has_more
    assert final.items == ()
    assert final.page is not None and not final.page.has_more
