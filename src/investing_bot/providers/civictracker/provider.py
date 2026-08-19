"""Live CivicTracker JSON provider with bounded, polite retries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import json
import random
from time import perf_counter
from typing import Any
from uuid import uuid4

from httpx import AsyncClient, RequestError, Response, TimeoutException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from investing_bot.models import (
    PageInfo,
    ProviderCapability,
    ProviderProvenance,
    ProviderResult,
    QuotaStatus,
    SocialPostRecord,
    SocialPostsRequest,
)
from investing_bot.providers.civictracker.canonical import (
    ADAPTER_VERSION,
    SCHEMA_VERSION,
    build_social_post,
)
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


JSON_PROVIDER_ID = "civictracker_json"
DEFAULT_ENDPOINT = (
    "https://civictracker.us/wp-json/civictracker/v1/proxy/social-posts"
)
DEFAULT_MEMBER_URL = "https://civictracker.us/executive/member/"


class CivicTrackerPostPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | str
    platform: str
    post_id: int | str
    content: str = ""
    posted_at: datetime
    media_urls: list[str] = Field(default_factory=list)
    original_url: str
    has_media: bool = False
    is_deleted: bool = False
    official_uuid: str | None = None


class CivicTrackerPagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    posts: list[CivicTrackerPostPayload]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool


class CivicTrackerProvider:
    """Read the documented WordPress JSON route without credentials."""

    def __init__(
        self,
        *,
        member_uuid: str,
        endpoint: str = DEFAULT_ENDPOINT,
        member_url: str = DEFAULT_MEMBER_URL,
        timeout_seconds: float = 15.0,
        retries: int = 2,
        user_agent: str = "InvestingBot/0.1 (+local personal research)",
        client: AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.member_uuid = member_uuid
        self.endpoint = endpoint
        self.member_url = member_url
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.user_agent = user_agent
        self._client = client
        self._sleep = sleep
        self._jitter = jitter
        self._now = now
        self._manifest = build_json_manifest()

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    async def fetch_social_posts(
        self, request: SocialPostsRequest
    ) -> ProviderResult[SocialPostRecord]:
        if request.member_id != self.member_uuid:
            self._raise(
                ProviderErrorCode.INVALID_REQUEST,
                "requested member does not match the configured CivicTracker member",
                retryable=False,
            )
        try:
            offset = int(request.cursor or "0")
        except ValueError:
            self._raise(
                ProviderErrorCode.INVALID_REQUEST,
                "CivicTracker cursor must be a non-negative offset",
                retryable=False,
            )
        if offset < 0:
            self._raise(
                ProviderErrorCode.INVALID_REQUEST,
                "CivicTracker cursor must be a non-negative offset",
                retryable=False,
            )

        response = await self._get(
            params={
                "limit": request.page_size,
                "offset": offset,
                "branch": "executive",
                "official_uuid": self.member_uuid,
            }
        )
        if _is_anonymous_offset_limit(response, offset=offset):
            return ProviderResult(
                items=(),
                provenance=ProviderProvenance(
                    capability=ProviderCapability.SOCIAL_POSTS,
                    provider_id=JSON_PROVIDER_ID,
                    adapter_version=ADAPTER_VERSION,
                    schema_version=SCHEMA_VERSION,
                    dataset_lineage="CivicTracker public social-post archive",
                    request_id=response.headers.get("x-request-id", str(uuid4())),
                ),
                page=PageInfo(next_cursor=None, has_more=False),
            )
        try:
            raw_page: Any = response.json()
            page = CivicTrackerPagePayload.model_validate(raw_page)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ProviderCallError(
                ProviderFailure(
                    provider_id=JSON_PROVIDER_ID,
                    capability=ProviderCapability.SOCIAL_POSTS,
                    code=ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                    safe_message="CivicTracker returned an incompatible JSON schema",
                    retryable=True,
                )
            ) from exc

        retrieved_at = self._now()
        try:
            if any(
                post.official_uuid is not None
                and post.official_uuid != self.member_uuid
                for post in page.posts
            ):
                raise ValueError("response contains a different official UUID")
            items = tuple(
                build_social_post(
                    provider_id=JSON_PROVIDER_ID,
                    platform=post.platform,
                    post_id=str(post.post_id),
                    content=post.content,
                    original_url=post.original_url,
                    published_at=post.posted_at,
                    has_media=post.has_media or bool(post.media_urls),
                    is_deleted=post.is_deleted,
                    retrieved_at=retrieved_at,
                    raw_payload=post.model_dump(mode="json"),
                )
                for post in page.posts
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ProviderCallError(
                ProviderFailure(
                    provider_id=JSON_PROVIDER_ID,
                    capability=ProviderCapability.SOCIAL_POSTS,
                    code=ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                    safe_message="CivicTracker post could not be normalized safely",
                    retryable=True,
                )
            ) from exc

        next_cursor = str(page.offset + page.limit) if page.has_more else None
        return ProviderResult(
            items=items,
            provenance=ProviderProvenance(
                capability=ProviderCapability.SOCIAL_POSTS,
                provider_id=JSON_PROVIDER_ID,
                adapter_version=ADAPTER_VERSION,
                schema_version=SCHEMA_VERSION,
                dataset_lineage="CivicTracker public social-post archive",
                request_id=response.headers.get("x-request-id", str(uuid4())),
            ),
            page=PageInfo(next_cursor=next_cursor, has_more=page.has_more),
            quota=_quota_from_headers(response),
        )

    async def test_connection(
        self,
        capability: ProviderCapability,
        credential_ref: CredentialReference | None,
    ) -> ConnectionTestResult:
        checked_at = self._now()
        if capability is not ProviderCapability.SOCIAL_POSTS:
            return ConnectionTestResult(
                provider_id=JSON_PROVIDER_ID,
                capability=capability,
                state=ProviderHealthState.UNSUPPORTED,
                checked_at=checked_at,
                latency_ms=0,
                error_code=ProviderErrorCode.UNSUPPORTED,
                message="capability is not supported",
            )
        started = perf_counter()
        try:
            result = await self.fetch_social_posts(
                SocialPostsRequest(member_id=self.member_uuid, page_size=1)
            )
        except ProviderCallError as exc:
            return ConnectionTestResult(
                provider_id=JSON_PROVIDER_ID,
                capability=capability,
                state=ProviderHealthState.UNHEALTHY,
                checked_at=checked_at,
                latency_ms=(perf_counter() - started) * 1_000,
                error_code=exc.failure.code,
                message=exc.failure.safe_message,
            )
        return ConnectionTestResult(
            provider_id=JSON_PROVIDER_ID,
            capability=capability,
            state=ProviderHealthState.HEALTHY,
            checked_at=checked_at,
            latency_ms=(perf_counter() - started) * 1_000,
            quota=result.quota,
        )

    async def _get(self, *, params: dict[str, object]) -> Response:
        headers = {"User-Agent": self.user_agent, "Referer": self.member_url}
        for attempt in range(self.retries + 1):
            try:
                if self._client is not None:
                    response = await self._client.get(
                        self.endpoint,
                        params=params,
                        headers=headers,
                        timeout=self.timeout_seconds,
                    )
                else:
                    async with AsyncClient(follow_redirects=True) as client:
                        response = await client.get(
                            self.endpoint,
                            params=params,
                            headers=headers,
                            timeout=self.timeout_seconds,
                        )
            except (TimeoutException, RequestError) as exc:
                if attempt < self.retries:
                    await self._sleep(0.5 * (2**attempt) + 0.1 * self._jitter())
                    continue
                raise ProviderCallError(
                    ProviderFailure(
                        provider_id=JSON_PROVIDER_ID,
                        capability=ProviderCapability.SOCIAL_POSTS,
                        code=ProviderErrorCode.TRANSPORT,
                        safe_message="CivicTracker could not be reached",
                        retryable=True,
                    )
                ) from exc

            if response.status_code < 400:
                return response
            if _is_anonymous_offset_limit(
                response, offset=int(params.get("offset", 0))
            ):
                return response
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self.retries:
                retry_after = _retry_after(response)
                if retry_after is None:
                    retry_after = 0.5 * (2**attempt) + 0.1 * self._jitter()
                await self._sleep(min(retry_after, 30.0))
                continue
            code = _error_code(response.status_code)
            raise ProviderCallError(
                ProviderFailure(
                    provider_id=JSON_PROVIDER_ID,
                    capability=ProviderCapability.SOCIAL_POSTS,
                    code=code,
                    safe_message=f"CivicTracker request failed with HTTP {response.status_code}",
                    retryable=retryable,
                    retry_after_seconds=_retry_after(response),
                )
            )
        raise AssertionError("retry loop must return or raise")

    @staticmethod
    def _raise(code: ProviderErrorCode, message: str, *, retryable: bool) -> None:
        raise ProviderCallError(
            ProviderFailure(
                provider_id=JSON_PROVIDER_ID,
                capability=ProviderCapability.SOCIAL_POSTS,
                code=code,
                safe_message=message,
                retryable=retryable,
            )
        )


def build_json_manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id=JSON_PROVIDER_ID,
        display_name="CivicTracker JSON",
        adapter_version=ADAPTER_VERSION,
        capabilities=frozenset({ProviderCapability.SOCIAL_POSTS}),
        schema_versions={ProviderCapability.SOCIAL_POSTS: SCHEMA_VERSION},
        authentication_required=False,
        rate_limit=RateLimitPolicy(requests=4, window_seconds=60),
        optional_features=frozenset({"pagination", "edits", "deletions", "media"}),
        allowed_origins=("https://civictracker.us",),
    )


def _error_code(status_code: int) -> ProviderErrorCode:
    if status_code in {401, 403}:
        return ProviderErrorCode.AUTHENTICATION
    if status_code == 404:
        return ProviderErrorCode.NOT_FOUND
    if status_code == 429:
        return ProviderErrorCode.RATE_LIMITED
    if status_code >= 500:
        return ProviderErrorCode.TEMPORARILY_UNAVAILABLE
    return ProviderErrorCode.INVALID_REQUEST


def _retry_after(response: Response) -> float | None:
    try:
        return max(0.0, float(response.headers["retry-after"]))
    except (KeyError, ValueError):
        return None


def _quota_from_headers(response: Response) -> QuotaStatus | None:
    try:
        limit = int(response.headers["x-ratelimit-limit"])
        remaining = int(response.headers["x-ratelimit-remaining"])
    except (KeyError, ValueError):
        return None
    return QuotaStatus(limit=limit, remaining=remaining)


def _is_anonymous_offset_limit(response: Response, *, offset: int) -> bool:
    """Recognize CivicTracker's explicit anonymous first-page restriction."""

    if response.status_code != 400 or offset <= 0:
        return False
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("code") == "invalid_argument_value"
        and "offset" in str(payload.get("message", "")).casefold()
        and "allowed values: 0" in str(payload.get("message", "")).casefold()
    )
