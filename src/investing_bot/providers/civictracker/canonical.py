"""Shared CivicTracker normalization for JSON and HTML adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from html import unescape
import json
import re
from typing import Any
import unicodedata
from zoneinfo import ZoneInfo

from investing_bot.models import CanonicalMetadata, SocialPostRecord


SCHEMA_VERSION = "social_post.v1"
ADAPTER_VERSION = "0.1.0"
_EASTERN = ZoneInfo("America/New_York")
_DISPLAY_DATE = "%b %d, %Y • %I:%M %p"
_WHITESPACE = re.compile(r"\s+")


def normalize_platform(value: str) -> str:
    platform = "_".join(re.findall(r"[a-z0-9]+", value.casefold()))
    if not platform:
        raise ValueError("platform must not be empty")
    return platform


def normalize_content(value: str | None) -> str:
    decoded = unescape(value or "")
    normalized = unicodedata.normalize("NFC", decoded)
    return _WHITESPACE.sub(" ", normalized).strip()


def parse_published_at(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.strptime(text, _DISPLAY_DATE).replace(tzinfo=_EASTERN)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("post timestamp must include a timezone")
    return parsed.astimezone(UTC)


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def build_social_post(
    *,
    provider_id: str,
    platform: str,
    post_id: str,
    content: str | None,
    original_url: str,
    published_at: str | datetime,
    has_media: bool,
    is_deleted: bool,
    retrieved_at: datetime,
    raw_payload: Any,
) -> SocialPostRecord:
    """Build the identical canonical shape for every CivicTracker transport."""

    event_at = parse_published_at(published_at)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieval timestamp must include a timezone")
    retrieved = retrieved_at.astimezone(UTC)
    normalized_content = normalize_content(content)
    media_only = not normalized_content and has_media and not is_deleted
    if not normalized_content and not has_media and not is_deleted:
        raise ValueError("active post has neither text nor media")
    identity = str(post_id).strip()
    if not identity or any(character.isspace() for character in identity):
        raise ValueError("post ID must be non-empty and contain no whitespace")
    url = original_url.strip()
    if not url.startswith("https://"):
        raise ValueError("original post URL must use HTTPS")
    content_hash = stable_hash(
        {
            "content": normalized_content,
            "is_deleted": is_deleted,
            "is_media_only": media_only,
            "original_url": url,
            "published_at": event_at.isoformat(),
        }
    )
    return SocialPostRecord(
        platform=normalize_platform(platform),
        post_id=identity,
        content=normalized_content,
        original_url=url,
        published_at=event_at,
        is_media_only=media_only,
        is_deleted=is_deleted,
        content_hash=content_hash,
        metadata=CanonicalMetadata(
            provider_id=provider_id,
            provider_record_id=identity,
            event_at=event_at,
            known_available_at=max(event_at, retrieved),
            retrieved_at=max(event_at, retrieved),
            raw_payload_hash=stable_hash(raw_payload),
            schema_version=SCHEMA_VERSION,
            adapter_version=ADAPTER_VERSION,
            dataset_lineage="CivicTracker public social-post archive",
        ),
    )
