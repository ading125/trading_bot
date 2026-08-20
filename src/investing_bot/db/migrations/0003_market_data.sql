CREATE TABLE IF NOT EXISTS universe_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    source_url VARCHAR NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    member_count INTEGER NOT NULL CHECK (member_count > 0)
);

CREATE TABLE IF NOT EXISTS universe_members (
    snapshot_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    company_name VARCHAR NOT NULL,
    sector VARCHAR NOT NULL,
    sub_industry VARCHAR NOT NULL,
    headquarters VARCHAR,
    date_added DATE,
    cik VARCHAR,
    founded VARCHAR,
    PRIMARY KEY (snapshot_id, symbol)
);

CREATE TABLE IF NOT EXISTS market_bars (
    provider_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    adjustment VARCHAR NOT NULL,
    bar_start TIMESTAMPTZ NOT NULL,
    bar_end TIMESTAMPTZ NOT NULL,
    session_date DATE NOT NULL,
    exchange_timezone VARCHAR NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    known_available_at TIMESTAMPTZ NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    adapter_version VARCHAR NOT NULL,
    dataset_lineage VARCHAR NOT NULL,
    repaired BOOLEAN NOT NULL,
    PRIMARY KEY (provider_id, symbol, interval, adjustment, bar_start)
);

CREATE INDEX IF NOT EXISTS market_bars_lookup_idx
    ON market_bars (symbol, interval, adjustment, bar_end);

CREATE TABLE IF NOT EXISTS market_bar_revisions (
    provider_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    adjustment VARCHAR NOT NULL,
    bar_start TIMESTAMPTZ NOT NULL,
    revision INTEGER NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (
        provider_id, symbol, interval, adjustment, bar_start, revision
    )
);

CREATE TABLE IF NOT EXISTS market_datasets (
    dataset_path VARCHAR PRIMARY KEY,
    provider_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    adjustment VARCHAR NOT NULL,
    partition_year INTEGER NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count > 0),
    first_bar_at TIMESTAMPTZ NOT NULL,
    last_bar_at TIMESTAMPTZ NOT NULL,
    file_hash VARCHAR NOT NULL,
    repaired BOOLEAN NOT NULL,
    published_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS market_quarantine (
    quarantine_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    provider_id VARCHAR NOT NULL,
    capability VARCHAR NOT NULL,
    request_hash VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    payload_hash VARCHAR NOT NULL,
    payload_json VARCHAR NOT NULL,
    quarantined_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS market_corporate_actions (
    provider_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    cash_amount DOUBLE,
    split_ratio DOUBLE,
    currency VARCHAR,
    raw_payload_hash VARCHAR NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider_id, symbol, action_type, effective_at)
);

CREATE TABLE IF NOT EXISTS market_quotes (
    provider_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    price DOUBLE NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    currency VARCHAR NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider_id, symbol, as_of)
);

CREATE TABLE IF NOT EXISTS market_news (
    provider_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    source_record_id VARCHAR NOT NULL,
    headline VARCHAR NOT NULL,
    summary VARCHAR NOT NULL,
    publisher VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    known_available_at TIMESTAMPTZ NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    PRIMARY KEY (provider_id, symbol, source_record_id)
);

CREATE TABLE IF NOT EXISTS market_earnings (
    provider_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    source_record_id VARCHAR NOT NULL,
    fiscal_period VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    reported_eps DOUBLE,
    estimated_eps DOUBLE,
    revenue DOUBLE,
    known_available_at TIMESTAMPTZ NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    PRIMARY KEY (provider_id, symbol, source_record_id)
);

CREATE TABLE IF NOT EXISTS market_collection_summaries (
    run_id VARCHAR PRIMARY KEY,
    provider_id VARCHAR NOT NULL,
    capability VARCHAR NOT NULL,
    requested_symbols INTEGER NOT NULL,
    stored_records INTEGER NOT NULL,
    duplicate_records INTEGER NOT NULL,
    revised_records INTEGER NOT NULL,
    quarantined BOOLEAN NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS market_collection_cursors (
    provider_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    adjustment VARCHAR NOT NULL,
    covered_through TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider_id, symbol, interval, adjustment)
);
