"""Normalize CivicTracker payloads into the application's canonical model.

The adapter deliberately accepts a retrieval timestamp instead of reading the
clock.  That makes fixture tests reproducible and ensures callers, rather than
low-level parsing code, decide when a collection occurred.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
import json
import re
from typing import Any
import unicodedata
from zoneinfo import ZoneInfo

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from investing_bot.models import SourcePost


PROVIDER_ID = "civictracker_json"
"""Stable identifier for the CivicTracker JSON adapter."""

_CIVICTRACKER_TIMEZONE = ZoneInfo("America/New_York")
_CIVICTRACKER_DATE_FORMAT = "%b %d, %Y • %I:%M %p"
_WHITESPACE = re.compile(r"\s+")
_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class CivicTrackerNormalizationError(ValueError):
    """Raised when a CivicTracker record cannot become a valid ``SourcePost``."""


def normalize_civictracker_post(
    raw_post: Mapping[str, Any],
    *,
    retrieved_at: datetime,
) -> SourcePost:
    """Convert one CivicTracker JSON post into a canonical ``SourcePost``.

    CivicTracker displays its timestamps in the local Eastern time zone.  The
    parsed timestamp is therefore localized to ``America/New_York`` (including
    daylight-saving rules) and then converted to UTC.

    ``content_hash`` is SHA-256 of stable canonical JSON containing the
    normalized content, UTC publication timestamp, original URL, and
    ``is_media_only`` flag.  Keys are sorted, separators contain no optional
    whitespace, and the resulting JSON is encoded as UTF-8. ``retrieved_at``
    is intentionally excluded so collecting the same post again produces the
    same digest.  Normalized content is HTML-unescaped, Unicode-NFC normalized,
    and has every run of whitespace collapsed to one ASCII space.

    Args:
        raw_post: A single mapping from the CivicTracker JSON response.
        retrieved_at: A timezone-aware timestamp supplied by the collector.

    Raises:
        CivicTrackerNormalizationError: If a required field is missing,
            malformed, internally inconsistent, or rejected by ``SourcePost``.
    """

    if not isinstance(raw_post, Mapping):
        raise CivicTrackerNormalizationError("post must be a mapping")

    normalized_retrieved_at = _normalize_retrieved_at(retrieved_at)
    post_id = _normalize_post_id(_required_field(raw_post, "id"))
    platform = _normalize_platform(_required_field(raw_post, "platform"))
    content = _normalize_content(_required_field(raw_post, "content"))
    published_at = _normalize_published_at(_required_field(raw_post, "date"))
    original_url = _normalize_url(_required_field(raw_post, "url"))
    has_media = _normalize_has_media(_required_field(raw_post, "has_media"))

    if not content and not has_media:
        raise CivicTrackerNormalizationError(
            "content is empty but has_media is false; the post has no usable payload"
        )

    is_media_only = not content and has_media
    content_hash = _calculate_content_hash(
        content=content,
        published_at=published_at,
        original_url=original_url.unicode_string(),
        is_media_only=is_media_only,
    )

    try:
        return SourcePost(
            provider_id=PROVIDER_ID,
            platform=platform,
            post_id=post_id,
            content=content,
            published_at=published_at,
            original_url=original_url,
            content_hash=content_hash,
            retrieved_at=normalized_retrieved_at,
            is_media_only=is_media_only,
        )
    except ValidationError as exc:
        raise CivicTrackerNormalizationError(
            f"post {post_id!r} failed canonical validation: {exc}"
        ) from exc


def normalize_post(
    raw_post: Mapping[str, Any],
    *,
    retrieved_at: datetime,
) -> SourcePost:
    """Concise alias for :func:`normalize_civictracker_post`."""

    return normalize_civictracker_post(raw_post, retrieved_at=retrieved_at)


def _required_field(raw_post: Mapping[str, Any], field: str) -> Any:
    try:
        return raw_post[field]
    except KeyError as exc:
        raise CivicTrackerNormalizationError(
            f"missing required CivicTracker field: {field}"
        ) from exc


def _normalize_post_id(value: Any) -> str:
    # bool is a subclass of int in Python, but it is never a meaningful post ID.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CivicTrackerNormalizationError("id must be an integer or string")

    post_id = str(value).strip()
    if not post_id:
        raise CivicTrackerNormalizationError("id must not be empty")
    if any(character.isspace() for character in post_id):
        raise CivicTrackerNormalizationError("id must not contain whitespace")
    return post_id


def _normalize_platform(value: Any) -> str:
    if not isinstance(value, str):
        raise CivicTrackerNormalizationError("platform must be a string")

    words = re.findall(r"[a-z0-9]+", value.casefold())
    platform = "_".join(words)
    if not platform:
        raise CivicTrackerNormalizationError("platform must not be empty")
    if platform != "truth_social":
        raise CivicTrackerNormalizationError(
            f"unsupported CivicTracker platform: {value!r}"
        )
    return platform


def _normalize_content(value: Any) -> str:
    if not isinstance(value, str):
        raise CivicTrackerNormalizationError("content must be a string")

    decoded = unescape(value)
    unicode_normalized = unicodedata.normalize("NFC", decoded)
    return _WHITESPACE.sub(" ", unicode_normalized).strip()


def _normalize_published_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CivicTrackerNormalizationError("date must be a string")

    try:
        local_time = datetime.strptime(value.strip(), _CIVICTRACKER_DATE_FORMAT)
    except ValueError as exc:
        raise CivicTrackerNormalizationError(
            "date must match 'Aug 3, 2026 • 9:50 AM'"
        ) from exc

    return local_time.replace(tzinfo=_CIVICTRACKER_TIMEZONE).astimezone(timezone.utc)


def _normalize_url(value: Any) -> AnyHttpUrl:
    if not isinstance(value, str):
        raise CivicTrackerNormalizationError("url must be a string")

    url = value.strip()
    if not url:
        raise CivicTrackerNormalizationError("url must not be empty")

    try:
        canonical_url = _HTTP_URL_ADAPTER.validate_python(url)
    except ValidationError as exc:
        raise CivicTrackerNormalizationError(
            f"url is not a valid HTTP(S) URL: {url!r}"
        ) from exc

    if canonical_url.scheme != "https":
        raise CivicTrackerNormalizationError("url must use HTTPS")
    if canonical_url.username is not None or canonical_url.password is not None:
        raise CivicTrackerNormalizationError("url must not contain user information")
    return canonical_url


def _normalize_has_media(value: Any) -> bool:
    if not isinstance(value, bool):
        raise CivicTrackerNormalizationError("has_media must be a boolean")
    return value


def _normalize_retrieved_at(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise CivicTrackerNormalizationError("retrieved_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CivicTrackerNormalizationError("retrieved_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _calculate_content_hash(
    *,
    content: str,
    published_at: datetime,
    original_url: str,
    is_media_only: bool,
) -> str:
    """Hash stable post content and metadata using canonical UTF-8 JSON.

    ``sort_keys`` fixes object-key order and compact separators remove optional
    formatting differences.  ``ensure_ascii=False`` keeps Unicode in its
    already-normalized form before UTF-8 encoding.
    """

    canonical_post = {
        "content": content,
        "is_media_only": is_media_only,
        "original_url": original_url,
        "published_at": published_at.astimezone(timezone.utc).isoformat(),
    }
    canonical_json = json.dumps(
        canonical_post,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical_json.encode("utf-8")).hexdigest()
