CREATE TABLE IF NOT EXISTS social_posts (
    platform VARCHAR NOT NULL,
    post_id VARCHAR NOT NULL,
    official_uuid VARCHAR NOT NULL,
    provider_id VARCHAR NOT NULL,
    content VARCHAR NOT NULL,
    original_url VARCHAR NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    is_media_only BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    discovery_eligible BOOLEAN NOT NULL,
    content_hash VARCHAR NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    adapter_version VARCHAR NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    current_revision INTEGER NOT NULL CHECK (current_revision >= 1),
    PRIMARY KEY (platform, post_id)
);

CREATE INDEX IF NOT EXISTS social_posts_published_idx
    ON social_posts (published_at);
CREATE INDEX IF NOT EXISTS social_posts_official_idx
    ON social_posts (official_uuid, published_at);

CREATE TABLE IF NOT EXISTS social_post_revisions (
    platform VARCHAR NOT NULL,
    post_id VARCHAR NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    content VARCHAR NOT NULL,
    is_media_only BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    content_hash VARCHAR NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (platform, post_id, revision)
);

CREATE TABLE IF NOT EXISTS collection_checkpoints (
    provider_id VARCHAR NOT NULL,
    official_uuid VARCHAR NOT NULL,
    boundary_platform VARCHAR,
    boundary_post_id VARCHAR,
    last_cursor VARCHAR,
    last_success_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider_id, official_uuid),
    CHECK (
        (boundary_platform IS NULL AND boundary_post_id IS NULL)
        OR (boundary_platform IS NOT NULL AND boundary_post_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS source_health (
    provider_id VARCHAR NOT NULL,
    capability VARCHAR NOT NULL,
    state VARCHAR NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL,
    last_success_at TIMESTAMPTZ,
    error_code VARCHAR,
    message VARCHAR,
    latency_ms DOUBLE NOT NULL CHECK (latency_ms >= 0),
    consecutive_failures INTEGER NOT NULL CHECK (consecutive_failures >= 0),
    next_poll_at TIMESTAMPTZ,
    PRIMARY KEY (provider_id, capability)
);

CREATE TABLE IF NOT EXISTS collection_summaries (
    run_id VARCHAR PRIMARY KEY,
    provider_id VARCHAR NOT NULL,
    official_uuid VARCHAR NOT NULL,
    pages_fetched INTEGER NOT NULL,
    new_posts INTEGER NOT NULL,
    duplicate_posts INTEGER NOT NULL,
    edited_posts INTEGER NOT NULL,
    skipped_discovery INTEGER NOT NULL,
    boundary_hit BOOLEAN NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);
