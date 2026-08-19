"""Persistence boundary for collected CivicTracker posts and checkpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict

from investing_bot.db.database import Database
from investing_bot.models import SocialPostRecord
from investing_bot.providers.contracts import ConnectionTestResult


class PostWriteKind(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    EDITED = "edited"


class StoredSocialPost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    post_id: str
    official_uuid: str
    provider_id: str
    content: str
    original_url: str
    published_at: AwareDatetime
    is_media_only: bool
    is_deleted: bool
    discovery_eligible: bool
    content_hash: str
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    current_revision: int


class CollectionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    official_uuid: str
    boundary_platform: str | None
    boundary_post_id: str | None
    last_cursor: str | None
    last_success_at: AwareDatetime | None
    updated_at: AwareDatetime


class CollectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    provider_id: str
    official_uuid: str
    pages_fetched: int
    new_posts: int
    duplicate_posts: int
    edited_posts: int
    skipped_discovery: int
    boundary_hit: bool
    recorded_at: AwareDatetime


class SocialPostRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def store(self, post: SocialPostRecord, *, official_uuid: str) -> PostWriteKind:
        observed_at = post.metadata.retrieved_at
        with self._database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT content_hash, current_revision
                FROM social_posts WHERE platform = ? AND post_id = ?
                """,
                [post.platform, post.post_id],
            ).fetchone()
            if existing is not None and str(existing[0]) == post.content_hash:
                connection.execute(
                    """
                    UPDATE social_posts
                    SET last_seen_at = ?, retrieved_at = ?, raw_payload_hash = ?
                    WHERE platform = ? AND post_id = ?
                    """,
                    [
                        observed_at,
                        observed_at,
                        post.metadata.raw_payload_hash,
                        post.platform,
                        post.post_id,
                    ],
                )
                return PostWriteKind.DUPLICATE

            revision = 1 if existing is None else int(existing[1]) + 1
            eligible = bool(post.content and not post.is_deleted and not post.is_media_only)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO social_posts (
                        platform, post_id, official_uuid, provider_id, content,
                        original_url, published_at, is_media_only, is_deleted,
                        discovery_eligible, content_hash, raw_payload_hash,
                        schema_version, adapter_version, retrieved_at,
                        first_seen_at, last_seen_at, current_revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        post.platform,
                        post.post_id,
                        official_uuid,
                        post.metadata.provider_id,
                        post.content,
                        post.original_url,
                        post.published_at,
                        post.is_media_only,
                        post.is_deleted,
                        eligible,
                        post.content_hash,
                        post.metadata.raw_payload_hash,
                        post.metadata.schema_version,
                        post.metadata.adapter_version,
                        observed_at,
                        observed_at,
                        observed_at,
                        revision,
                    ],
                )
                kind = PostWriteKind.NEW
            else:
                connection.execute(
                    """
                    UPDATE social_posts SET
                        official_uuid = ?, provider_id = ?, content = ?,
                        original_url = ?, published_at = ?, is_media_only = ?,
                        is_deleted = ?, discovery_eligible = ?, content_hash = ?,
                        raw_payload_hash = ?, schema_version = ?, adapter_version = ?,
                        retrieved_at = ?, last_seen_at = ?, current_revision = ?
                    WHERE platform = ? AND post_id = ?
                    """,
                    [
                        official_uuid,
                        post.metadata.provider_id,
                        post.content,
                        post.original_url,
                        post.published_at,
                        post.is_media_only,
                        post.is_deleted,
                        eligible,
                        post.content_hash,
                        post.metadata.raw_payload_hash,
                        post.metadata.schema_version,
                        post.metadata.adapter_version,
                        observed_at,
                        observed_at,
                        revision,
                        post.platform,
                        post.post_id,
                    ],
                )
                kind = PostWriteKind.EDITED
            connection.execute(
                """
                INSERT INTO social_post_revisions (
                    platform, post_id, revision, content, is_media_only,
                    is_deleted, content_hash, raw_payload_hash, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    post.platform,
                    post.post_id,
                    revision,
                    post.content,
                    post.is_media_only,
                    post.is_deleted,
                    post.content_hash,
                    post.metadata.raw_payload_hash,
                    observed_at,
                ],
            )
        return kind

    def get_checkpoint(
        self, *, provider_id: str, official_uuid: str
    ) -> CollectionCheckpoint | None:
        row = self._database.fetchone(
            """
            SELECT provider_id, official_uuid, boundary_platform, boundary_post_id,
                   last_cursor, last_success_at, updated_at
            FROM collection_checkpoints
            WHERE provider_id = ? AND official_uuid = ?
            """,
            [provider_id, official_uuid],
        )
        return None if row is None else CollectionCheckpoint(
            provider_id=str(row[0]),
            official_uuid=str(row[1]),
            boundary_platform=None if row[2] is None else str(row[2]),
            boundary_post_id=None if row[3] is None else str(row[3]),
            last_cursor=None if row[4] is None else str(row[4]),
            last_success_at=row[5],
            updated_at=row[6],
        )

    def save_checkpoint(
        self,
        *,
        provider_id: str,
        official_uuid: str,
        boundary: tuple[str, str] | None,
        last_cursor: str | None,
        succeeded_at: datetime,
    ) -> None:
        platform, post_id = boundary or (None, None)
        self._database.execute(
            """
            INSERT INTO collection_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider_id, official_uuid) DO UPDATE SET
                boundary_platform = excluded.boundary_platform,
                boundary_post_id = excluded.boundary_post_id,
                last_cursor = excluded.last_cursor,
                last_success_at = excluded.last_success_at,
                updated_at = excluded.updated_at
            """,
            [
                provider_id,
                official_uuid,
                platform,
                post_id,
                last_cursor,
                succeeded_at,
                succeeded_at,
            ],
        )

    def record_health(
        self,
        result: ConnectionTestResult,
        *,
        next_poll_at: datetime | None,
    ) -> None:
        prior = self._database.fetchone(
            """
            SELECT last_success_at, consecutive_failures FROM source_health
            WHERE provider_id = ? AND capability = ?
            """,
            [result.provider_id, result.capability.value],
        )
        available = result.available
        last_success = result.checked_at if available else (prior[0] if prior else None)
        failures = 0 if available else (int(prior[1]) + 1 if prior else 1)
        self._database.execute(
            """
            INSERT INTO source_health VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider_id, capability) DO UPDATE SET
                state = excluded.state, checked_at = excluded.checked_at,
                last_success_at = excluded.last_success_at,
                error_code = excluded.error_code, message = excluded.message,
                latency_ms = excluded.latency_ms,
                consecutive_failures = excluded.consecutive_failures,
                next_poll_at = excluded.next_poll_at
            """,
            [
                result.provider_id,
                result.capability.value,
                result.state.value,
                result.checked_at,
                last_success,
                result.error_code.value if result.error_code else None,
                result.message,
                result.latency_ms,
                failures,
                next_poll_at,
            ],
        )

    def list_posts(self, *, limit: int = 100) -> list[StoredSocialPost]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self._database.fetchall(
            """
            SELECT platform, post_id, official_uuid, provider_id, content,
                   original_url, published_at, is_media_only, is_deleted,
                   discovery_eligible, content_hash, first_seen_at, last_seen_at,
                   current_revision
            FROM social_posts ORDER BY published_at DESC LIMIT ?
            """,
            [limit],
        )
        return [StoredSocialPost(**dict(zip(_POST_FIELDS, row, strict=True))) for row in rows]

    def count_posts(self) -> int:
        row = self._database.fetchone("SELECT COUNT(*) FROM social_posts")
        return 0 if row is None else int(row[0])

    def list_health(self) -> list[dict[str, object]]:
        rows = self._database.fetchall(
            """
            SELECT provider_id, capability, state, checked_at, last_success_at,
                   error_code, message, latency_ms, consecutive_failures, next_poll_at
            FROM source_health ORDER BY capability, provider_id
            """
        )
        fields = (
            "provider_id", "capability", "state", "checked_at", "last_success_at",
            "error_code", "message", "latency_ms", "consecutive_failures", "next_poll_at",
        )
        return [dict(zip(fields, row, strict=True)) for row in rows]

    def record_summary(self, summary: CollectionSummary) -> None:
        self._database.execute(
            "INSERT INTO collection_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(summary.model_dump().values()),
        )


_POST_FIELDS = (
    "platform", "post_id", "official_uuid", "provider_id", "content",
    "original_url", "published_at", "is_media_only", "is_deleted",
    "discovery_eligible", "content_hash", "first_seen_at", "last_seen_at",
    "current_revision",
)


def utc_now() -> datetime:
    return datetime.now(UTC)
