"""Defensive HTML fallback for CivicTracker's social-post cards."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from bs4 import BeautifulSoup, Tag
from httpx import AsyncClient, RequestError, TimeoutException

from investing_bot.models import (
    PageInfo,
    ProviderCapability,
    ProviderProvenance,
    ProviderResult,
    SocialPostRecord,
    SocialPostsRequest,
)
from investing_bot.providers.civictracker.canonical import (
    ADAPTER_VERSION,
    SCHEMA_VERSION,
    build_social_post,
)
from investing_bot.providers.civictracker.provider import DEFAULT_MEMBER_URL
from investing_bot.providers.contracts import (
    ConnectionTestResult,
    CredentialReference,
    ProviderCallError,
    ProviderErrorCode,
    ProviderFailure,
    ProviderHealthState,
    ProviderManifest,
    RateLimitPolicy,
)


HTML_PROVIDER_ID = "civictracker_html"


class CivicTrackerHtmlProvider:
    """Parse server-rendered ``.social-post`` cards when JSON is unavailable."""

    def __init__(
        self,
        *,
        member_uuid: str,
        member_url: str = DEFAULT_MEMBER_URL,
        timeout_seconds: float = 15.0,
        user_agent: str = "InvestingBot/0.1 (+local personal research)",
        client: AsyncClient | None = None,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.member_uuid = member_uuid
        self.member_url = member_url
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self._client = client
        self._now = now
        self._manifest = build_html_manifest()

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    async def fetch_social_posts(
        self, request: SocialPostsRequest
    ) -> ProviderResult[SocialPostRecord]:
        if request.member_id != self.member_uuid or request.cursor not in {None, "0"}:
            raise ProviderCallError(
                ProviderFailure(
                    provider_id=HTML_PROVIDER_ID,
                    capability=ProviderCapability.SOCIAL_POSTS,
                    code=ProviderErrorCode.INVALID_REQUEST,
                    safe_message="HTML fallback supports only its configured member and first page",
                    retryable=False,
                )
            )
        headers = {"User-Agent": self.user_agent, "Referer": self.member_url}
        try:
            if self._client is not None:
                response = await self._client.get(
                    self.member_url,
                    params={"uuid": self.member_uuid},
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            else:
                async with AsyncClient(follow_redirects=True) as client:
                    response = await client.get(
                        self.member_url,
                        params={"uuid": self.member_uuid},
                        headers=headers,
                        timeout=self.timeout_seconds,
                    )
        except (TimeoutException, RequestError) as exc:
            raise ProviderCallError(
                ProviderFailure(
                    provider_id=HTML_PROVIDER_ID,
                    capability=ProviderCapability.SOCIAL_POSTS,
                    code=ProviderErrorCode.TRANSPORT,
                    safe_message="CivicTracker HTML page could not be reached",
                    retryable=True,
                )
            ) from exc
        if response.status_code >= 400:
            raise ProviderCallError(
                ProviderFailure(
                    provider_id=HTML_PROVIDER_ID,
                    capability=ProviderCapability.SOCIAL_POSTS,
                    code=(
                        ProviderErrorCode.TEMPORARILY_UNAVAILABLE
                        if response.status_code >= 500
                        else ProviderErrorCode.INVALID_REQUEST
                    ),
                    safe_message=f"CivicTracker HTML request failed with HTTP {response.status_code}",
                    retryable=response.status_code >= 500,
                )
            )
        items = parse_social_post_html(
            response.text,
            retrieved_at=self._now(),
            limit=request.page_size,
        )
        if not items:
            raise ProviderCallError(
                ProviderFailure(
                    provider_id=HTML_PROVIDER_ID,
                    capability=ProviderCapability.SOCIAL_POSTS,
                    code=ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                    safe_message="CivicTracker HTML contains no parseable social-post cards",
                    retryable=True,
                )
            )
        return ProviderResult(
            items=items,
            provenance=ProviderProvenance(
                capability=ProviderCapability.SOCIAL_POSTS,
                provider_id=HTML_PROVIDER_ID,
                adapter_version=ADAPTER_VERSION,
                schema_version=SCHEMA_VERSION,
                dataset_lineage="CivicTracker public social-post archive (HTML fallback)",
                request_id=response.headers.get("x-request-id", str(uuid4())),
            ),
            page=PageInfo(next_cursor=None, has_more=False),
        )

    async def test_connection(
        self,
        capability: ProviderCapability,
        credential_ref: CredentialReference | None,
    ) -> ConnectionTestResult:
        checked_at = self._now()
        if capability is not ProviderCapability.SOCIAL_POSTS:
            return ConnectionTestResult(
                provider_id=HTML_PROVIDER_ID,
                capability=capability,
                state=ProviderHealthState.UNSUPPORTED,
                checked_at=checked_at,
                latency_ms=0,
                error_code=ProviderErrorCode.UNSUPPORTED,
                message="capability is not supported",
            )
        started = perf_counter()
        try:
            await self.fetch_social_posts(
                SocialPostsRequest(member_id=self.member_uuid, page_size=1)
            )
        except ProviderCallError as exc:
            return ConnectionTestResult(
                provider_id=HTML_PROVIDER_ID,
                capability=capability,
                state=ProviderHealthState.UNHEALTHY,
                checked_at=checked_at,
                latency_ms=(perf_counter() - started) * 1_000,
                error_code=exc.failure.code,
                message=exc.failure.safe_message,
            )
        return ConnectionTestResult(
            provider_id=HTML_PROVIDER_ID,
            capability=capability,
            state=ProviderHealthState.HEALTHY,
            checked_at=checked_at,
            latency_ms=(perf_counter() - started) * 1_000,
        )


def parse_social_post_html(
    html: str,
    *,
    retrieved_at: datetime,
    limit: int = 100,
) -> tuple[SocialPostRecord, ...]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[SocialPostRecord] = []
    for card in soup.select(".social-post")[:limit]:
        if not isinstance(card, Tag):
            continue
        raw = _extract_card(card)
        try:
            records.append(
                build_social_post(
                    provider_id=HTML_PROVIDER_ID,
                    platform=raw["platform"],
                    post_id=raw["post_id"],
                    content=raw["content"],
                    original_url=raw["original_url"],
                    published_at=raw["published_at"],
                    has_media=raw["has_media"],
                    is_deleted=raw["is_deleted"],
                    retrieved_at=retrieved_at,
                    raw_payload=raw,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(records)


def build_html_manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id=HTML_PROVIDER_ID,
        display_name="CivicTracker HTML fallback",
        adapter_version=ADAPTER_VERSION,
        capabilities=frozenset({ProviderCapability.SOCIAL_POSTS}),
        schema_versions={ProviderCapability.SOCIAL_POSTS: SCHEMA_VERSION},
        rate_limit=RateLimitPolicy(requests=2, window_seconds=60),
        optional_features=frozenset({"fixture_backed", "media"}),
        allowed_origins=("https://civictracker.us",),
    )


def _extract_card(card: Tag) -> dict[str, Any]:
    link = card.select_one("a.post-original-link, a.original-post, a[href*='truthsocial']")
    date_node = card.select_one("time, .post-date-bottom, .post-date")
    content_node = card.select_one(".post-content")
    if link is None or date_node is None:
        raise ValueError("required post fields are absent")
    url = str(link.get("href") or "")
    post_id = str(
        card.get("data-post-id")
        or card.get("data-id")
        or url.rstrip("/").rsplit("/", 1)[-1]
    )
    published_at = str(date_node.get("datetime") or date_node.get_text(" ", strip=True))
    classes = {str(value).casefold() for value in card.get("class", [])}
    platform = str(card.get("data-platform") or "Truth Social")
    return {
        "platform": platform,
        "post_id": post_id,
        "content": content_node.get_text(" ", strip=True) if content_node else "",
        "original_url": url,
        "published_at": published_at,
        "has_media": card.select_one("img, video, .post-media") is not None,
        "is_deleted": "deleted" in classes or card.get("data-deleted") == "true",
    }
