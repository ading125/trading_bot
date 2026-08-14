"""Contract tests for the CivicTracker-to-SourcePost adapter.

The tests deliberately start from a saved provider response.  This keeps the
lesson deterministic and lets the adapter be exercised without network access.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pytest

from investing_bot.providers.civictracker import (
    CivicTrackerNormalizationError,
    normalize_civictracker_post,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "civictracker_posts.json"
RETRIEVED_AT = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def raw_posts() -> list[dict[str, Any]]:
    """Load the immutable example payload once for this test module."""

    with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def test_normalizes_a_regular_post(raw_posts: list[dict[str, Any]]) -> None:
    post = normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)

    assert post.provider_id == "civictracker_json"
    assert post.platform == "truth_social"
    assert post.post_id == "653387"
    assert post.identity == ("truth_social", "653387")
    assert post.content == (
        "Mike Wirth, Chairman and CEO of Chevron, just gave an interview about "
        "why his company is doing so well. Chevron is back in Venezuela, far "
        "bigger & stronger than before."
    )
    assert post.original_url.unicode_string() == (
        "https://truthsocial.com/@realDonaldTrump/117031897808226413"
    )
    assert post.published_at == datetime(2026, 8, 3, 13, 50, tzinfo=timezone.utc)
    assert post.retrieved_at == RETRIEVED_AT
    assert post.is_media_only is False
    assert re.fullmatch(r"[0-9a-f]{64}", post.content_hash)


def test_exact_duplicate_has_same_identity_and_hash(
    raw_posts: list[dict[str, Any]],
) -> None:
    first = normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)
    duplicate = normalize_civictracker_post(raw_posts[1], retrieved_at=RETRIEVED_AT)

    assert duplicate.identity == first.identity
    assert duplicate.content_hash == first.content_hash


def test_edited_post_keeps_identity_but_changes_hash(
    raw_posts: list[dict[str, Any]],
) -> None:
    original = normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)
    edited = normalize_civictracker_post(raw_posts[2], retrieved_at=RETRIEVED_AT)

    assert edited.identity == original.identity
    assert edited.content_hash != original.content_hash


def test_empty_post_with_media_is_a_valid_media_only_post(
    raw_posts: list[dict[str, Any]],
) -> None:
    post = normalize_civictracker_post(raw_posts[3], retrieved_at=RETRIEVED_AT)

    assert post.identity == ("truth_social", "653510")
    assert post.content == ""
    assert post.is_media_only is True
    assert re.fullmatch(r"[0-9a-f]{64}", post.content_hash)


def test_decodes_html_entities_and_collapses_whitespace(
    raw_posts: list[dict[str, Any]],
) -> None:
    raw_post = deepcopy(raw_posts[0])
    raw_post["content"] = "  Chevron\t&amp;\n\nExxon&nbsp; Mobil  "

    post = normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)

    assert post.content == "Chevron & Exxon Mobil"


def test_interprets_civictracker_time_as_new_york_and_converts_to_utc(
    raw_posts: list[dict[str, Any]],
) -> None:
    post = normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)

    # August is daylight-saving time in New York, so 09:50 EDT is 13:50 UTC.
    assert post.published_at == datetime(2026, 8, 3, 13, 50, tzinfo=timezone.utc)
    assert post.published_at.utcoffset() == timedelta(0)


def test_uses_explicit_retrieval_time_and_normalizes_it_to_utc(
    raw_posts: list[dict[str, Any]],
) -> None:
    eastern_offset = timezone(timedelta(hours=-4))
    supplied_time = datetime(2026, 8, 4, 8, 30, tzinfo=eastern_offset)

    post = normalize_civictracker_post(raw_posts[0], retrieved_at=supplied_time)

    assert post.retrieved_at == datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)
    assert post.retrieved_at.utcoffset() == timedelta(0)


def test_retrieval_time_does_not_change_content_hash(
    raw_posts: list[dict[str, Any]],
) -> None:
    first = normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)
    later = normalize_civictracker_post(
        raw_posts[0],
        retrieved_at=RETRIEVED_AT + timedelta(minutes=15),
    )

    assert later.retrieved_at != first.retrieved_at
    assert later.content_hash == first.content_hash


def test_hash_matches_the_documented_canonical_payload(
    raw_posts: list[dict[str, Any]],
) -> None:
    post = normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)
    hash_payload = {
        "content": post.content,
        "is_media_only": post.is_media_only,
        "original_url": post.original_url.unicode_string(),
        "published_at": post.published_at.isoformat(),
    }
    serialized = json.dumps(
        hash_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert post.content_hash == hashlib.sha256(serialized).hexdigest()


def test_equivalent_url_spellings_have_same_canonical_url_and_hash(
    raw_posts: list[dict[str, Any]],
) -> None:
    alternate_spelling = deepcopy(raw_posts[0])
    alternate_spelling["url"] = (
        "HTTPS://TRUTHSOCIAL.COM/@realDonaldTrump/117031897808226413"
    )

    original = normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)
    alternate = normalize_civictracker_post(
        alternate_spelling,
        retrieved_at=RETRIEVED_AT,
    )

    assert alternate.original_url == original.original_url
    assert alternate.content_hash == original.content_hash


@pytest.mark.parametrize(
    "missing_field",
    ["id", "platform", "content", "date", "url", "has_media"],
)
def test_rejects_missing_required_fields(
    raw_posts: list[dict[str, Any]],
    missing_field: str,
) -> None:
    raw_post = deepcopy(raw_posts[0])
    del raw_post[missing_field]

    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)


@pytest.mark.parametrize(
    "invalid_id",
    [None, "", "   ", True, [], {}],
)
def test_rejects_malformed_post_ids(
    raw_posts: list[dict[str, Any]],
    invalid_id: object,
) -> None:
    raw_post = deepcopy(raw_posts[0])
    raw_post["id"] = invalid_id

    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)


@pytest.mark.parametrize(
    "invalid_date",
    [None, "", "2026-08-03T09:50:00", "Aug 32, 2026 • 9:50 AM", 653387],
)
def test_rejects_invalid_dates(
    raw_posts: list[dict[str, Any]],
    invalid_date: object,
) -> None:
    raw_post = deepcopy(raw_posts[0])
    raw_post["date"] = invalid_date

    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)


@pytest.mark.parametrize(
    "invalid_retrieved_at",
    [datetime(2026, 8, 4, 12, 0), "2026-08-04T12:00:00Z", None],
)
def test_rejects_naive_or_non_datetime_retrieval_times(
    raw_posts: list[dict[str, Any]],
    invalid_retrieved_at: object,
) -> None:
    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(  # type: ignore[arg-type]
            raw_posts[0],
            retrieved_at=invalid_retrieved_at,
        )


@pytest.mark.parametrize("invalid_payload", [None, [], "post", 653387])
def test_rejects_non_mapping_payloads(invalid_payload: object) -> None:
    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(  # type: ignore[arg-type]
            invalid_payload,
            retrieved_at=RETRIEVED_AT,
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("content", None),
        ("content", ["Chevron"]),
        ("url", None),
        ("url", ""),
        ("has_media", 0),
        ("has_media", "false"),
    ],
)
def test_rejects_malformed_raw_field_values(
    raw_posts: list[dict[str, Any]],
    field: str,
    invalid_value: object,
) -> None:
    raw_post = deepcopy(raw_posts[0])
    raw_post[field] = invalid_value

    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)


def test_ignores_unknown_provider_fields(raw_posts: list[dict[str, Any]]) -> None:
    raw_post = deepcopy(raw_posts[0])
    raw_post["new_provider_field"] = {"shape": "may change"}

    baseline = normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)
    with_extra = normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)

    assert with_extra == baseline


@pytest.mark.parametrize("platform", ["", "x", "facebook", 123, None])
def test_rejects_unsupported_or_malformed_platforms(
    raw_posts: list[dict[str, Any]],
    platform: object,
) -> None:
    raw_post = deepcopy(raw_posts[0])
    raw_post["platform"] = platform

    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)


def test_rejects_empty_text_when_no_media_is_present(
    raw_posts: list[dict[str, Any]],
) -> None:
    raw_post = deepcopy(raw_posts[0])
    raw_post["content"] = " \n\t "
    raw_post["has_media"] = False

    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)


def test_wraps_canonical_model_validation_failures(
    raw_posts: list[dict[str, Any]],
) -> None:
    raw_post = deepcopy(raw_posts[0])
    raw_post["url"] = "http://truthsocial.com/insecure-post"

    with pytest.raises(CivicTrackerNormalizationError) as error_info:
        normalize_civictracker_post(raw_post, retrieved_at=RETRIEVED_AT)

    assert isinstance(error_info.value, ValueError)


def test_rejects_retrieval_before_publication(
    raw_posts: list[dict[str, Any]],
) -> None:
    before_publication = datetime(2026, 8, 3, 13, 49, tzinfo=timezone.utc)

    with pytest.raises(CivicTrackerNormalizationError):
        normalize_civictracker_post(
            raw_posts[0],
            retrieved_at=before_publication,
        )
