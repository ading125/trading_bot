CREATE TABLE IF NOT EXISTS company_aliases (
    normalized_alias VARCHAR NOT NULL,
    alias VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    company_name VARCHAR NOT NULL,
    alias_version VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL,
    active BOOLEAN NOT NULL,
    PRIMARY KEY (normalized_alias, symbol, alias_version)
);

CREATE INDEX IF NOT EXISTS company_alias_lookup_idx
    ON company_aliases (normalized_alias, active);

CREATE TABLE IF NOT EXISTS entity_resolutions (
    resolution_id VARCHAR PRIMARY KEY,
    source_type VARCHAR NOT NULL,
    source_record_id VARCHAR NOT NULL,
    mention_text VARCHAR NOT NULL,
    normalized_mention VARCHAR NOT NULL,
    source_excerpt VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    symbol VARCHAR,
    company_name VARCHAR,
    confidence DOUBLE NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    extraction_method VARCHAR NOT NULL,
    alias_version VARCHAR,
    provider_id VARCHAR,
    provider_request_id VARCHAR,
    alternatives_json VARCHAR NOT NULL,
    reason VARCHAR,
    resolved_at TIMESTAMPTZ NOT NULL,
    UNIQUE (source_type, source_record_id, normalized_mention)
);

CREATE TABLE IF NOT EXISTS candidate_evidence (
    evidence_id VARCHAR PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    company_name VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    source_record_id VARCHAR NOT NULL,
    source_excerpt VARCHAR NOT NULL,
    source_url VARCHAR,
    event_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    extraction_method VARCHAR NOT NULL,
    resolution_id VARCHAR,
    resolution_confidence DOUBLE NOT NULL CHECK (
        resolution_confidence >= 0 AND resolution_confidence <= 1
    ),
    relevance DOUBLE NOT NULL CHECK (relevance >= 0 AND relevance <= 1),
    expires_at TIMESTAMPTZ NOT NULL,
    active BOOLEAN NOT NULL,
    UNIQUE (symbol, source_type, source_record_id)
);

CREATE INDEX IF NOT EXISTS candidate_evidence_active_idx
    ON candidate_evidence (symbol, active, expires_at);

CREATE TABLE IF NOT EXISTS candidates (
    symbol VARCHAR PRIMARY KEY,
    company_name VARCHAR NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    highest_confidence DOUBLE NOT NULL CHECK (
        highest_confidence >= 0 AND highest_confidence <= 1
    ),
    active BOOLEAN NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_refresh_summaries (
    run_id VARCHAR PRIMARY KEY,
    sources_scanned INTEGER NOT NULL,
    resolutions_recorded INTEGER NOT NULL,
    candidates_active INTEGER NOT NULL,
    evidence_active INTEGER NOT NULL,
    manual_review_count INTEGER NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);
