from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from investing_bot.db import Database, PostWriteKind, SocialPostRepository
from investing_bot.providers.civictracker.canonical import build_social_post


MEMBER = "3094abf7-4a95-4b8d-8c8d-af7d1c3747a1"
NOW = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


def make_post(content: str, *, deleted: bool = False, observed=NOW):
    return build_social_post(
        provider_id="civictracker_json",
        platform="Truth Social",
        post_id="123",
        content=content,
        original_url="https://truthsocial.com/@example/posts/123",
        published_at="2026-08-19T14:30:00+00:00",
        has_media=False,
        is_deleted=deleted,
        retrieved_at=observed,
        raw_payload={"content": content, "deleted": deleted},
    )


def test_storage_deduplicates_and_records_edits_and_deletions(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.duckdb")
    database.connect()
    database.migrate()
    repository = SocialPostRepository(database)

    assert repository.store(make_post("first"), official_uuid=MEMBER) is PostWriteKind.NEW
    assert repository.store(make_post("first"), official_uuid=MEMBER) is PostWriteKind.DUPLICATE
    assert repository.store(
        make_post("edited", observed=NOW + timedelta(minutes=1)),
        official_uuid=MEMBER,
    ) is PostWriteKind.EDITED
    assert repository.store(
        make_post("", deleted=True, observed=NOW + timedelta(minutes=2)),
        official_uuid=MEMBER,
    ) is PostWriteKind.EDITED

    stored = repository.list_posts()[0]
    assert stored.current_revision == 3
    assert stored.is_deleted
    assert not stored.discovery_eligible
    assert database.fetchone(
        "SELECT COUNT(*) FROM social_post_revisions WHERE post_id = '123'"
    ) == (3,)
    database.close()
