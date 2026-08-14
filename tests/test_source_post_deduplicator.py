"""Behavior tests for the in-memory source-post deduplication service.

These tests exercise the service with canonical posts produced by the real
CivicTracker normalizer.  That keeps the boundary realistic while leaving
storage concerns out of this first in-memory implementation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pytest

from investing_bot.models import SourcePost
from investing_bot.providers.civictracker import normalize_civictracker_post
from investing_bot.services import DeduplicationOutcome, SourcePostDeduplicator


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "civictracker_posts.json"
RETRIEVED_AT = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def raw_posts() -> list[dict[str, Any]]:
    """Load the saved CivicTracker examples used by the adapter tests."""

    with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


@pytest.fixture
def regular_post(raw_posts: list[dict[str, Any]]) -> SourcePost:
    """Return the fixture's first Chevron post in canonical form."""

    return normalize_civictracker_post(raw_posts[0], retrieved_at=RETRIEVED_AT)


def test_first_observation_is_new_and_becomes_current(
    regular_post: SourcePost,
) -> None:
    deduplicator = SourcePostDeduplicator()

    result = deduplicator.process(regular_post)

    assert result.outcome is DeduplicationOutcome.NEW
    assert result.creates_event is True
    assert result.current is regular_post
    assert result.previous is None
    assert deduplicator.get(regular_post.identity) is regular_post
    assert len(deduplicator) == 1


def test_exact_duplicate_does_not_create_event_or_replace_current(
    raw_posts: list[dict[str, Any]],
    regular_post: SourcePost,
) -> None:
    deduplicator = SourcePostDeduplicator()
    deduplicator.process(regular_post)
    duplicate = normalize_civictracker_post(
        raw_posts[1],
        retrieved_at=RETRIEVED_AT,
    )

    result = deduplicator.process(duplicate)

    assert result.outcome is DeduplicationOutcome.DUPLICATE
    assert result.creates_event is False
    assert result.current is regular_post
    assert result.previous is None
    assert deduplicator.get(regular_post.identity) is regular_post
    assert len(deduplicator) == 1


def test_edited_observation_reports_both_revisions_and_becomes_current(
    raw_posts: list[dict[str, Any]],
    regular_post: SourcePost,
) -> None:
    deduplicator = SourcePostDeduplicator()
    deduplicator.process(regular_post)
    edited_post = normalize_civictracker_post(
        raw_posts[2],
        retrieved_at=RETRIEVED_AT,
    )

    result = deduplicator.process(edited_post)

    assert result.outcome is DeduplicationOutcome.EDITED
    assert result.creates_event is True
    assert result.current is edited_post
    assert result.previous is regular_post
    assert result.previous.content_hash != result.current.content_hash
    assert deduplicator.get(edited_post.identity) is edited_post
    assert len(deduplicator) == 1


def test_distinct_identities_are_tracked_independently(
    raw_posts: list[dict[str, Any]],
    regular_post: SourcePost,
) -> None:
    deduplicator = SourcePostDeduplicator()
    media_only_post = normalize_civictracker_post(
        raw_posts[3],
        retrieved_at=RETRIEVED_AT,
    )

    regular_result = deduplicator.process(regular_post)
    media_result = deduplicator.process(media_only_post)

    assert regular_result.outcome is DeduplicationOutcome.NEW
    assert media_result.outcome is DeduplicationOutcome.NEW
    assert regular_post.identity != media_only_post.identity
    assert deduplicator.get(regular_post.identity) is regular_post
    assert deduplicator.get(media_only_post.identity) is media_only_post
    assert deduplicator.get(("truth_social", "not-seen")) is None
    assert len(deduplicator) == 2


def test_later_retrieval_with_same_hash_keeps_original_stored_record(
    raw_posts: list[dict[str, Any]],
    regular_post: SourcePost,
) -> None:
    deduplicator = SourcePostDeduplicator()
    deduplicator.process(regular_post)
    later_observation = normalize_civictracker_post(
        raw_posts[0],
        retrieved_at=RETRIEVED_AT + timedelta(minutes=15),
    )

    result = deduplicator.process(later_observation)

    assert later_observation.retrieved_at != regular_post.retrieved_at
    assert later_observation.content_hash == regular_post.content_hash
    assert result.outcome is DeduplicationOutcome.DUPLICATE
    assert result.creates_event is False
    assert result.current is regular_post
    assert result.previous is None
    assert deduplicator.get(regular_post.identity) is regular_post
    assert deduplicator.get(regular_post.identity) is not later_observation
    assert len(deduplicator) == 1
