CREATE TABLE IF NOT EXISTS market_discovery_scans (
    symbol VARCHAR PRIMARY KEY,
    last_scanned_at TIMESTAMPTZ NOT NULL,
    succeeded BOOLEAN NOT NULL,
    news_records INTEGER NOT NULL CHECK (news_records >= 0),
    earnings_records INTEGER NOT NULL CHECK (earnings_records >= 0)
);

CREATE INDEX IF NOT EXISTS market_discovery_scans_rotation_idx
    ON market_discovery_scans (last_scanned_at, symbol);
