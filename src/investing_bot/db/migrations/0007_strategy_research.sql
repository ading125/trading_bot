CREATE TABLE IF NOT EXISTS strategy_evaluations (
    evaluation_id VARCHAR PRIMARY KEY,
    cache_key VARCHAR NOT NULL UNIQUE,
    assessment_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    strategy_id VARCHAR NOT NULL,
    strategy_version VARCHAR NOT NULL,
    research_status VARCHAR NOT NULL CHECK (
        research_status IN ('hypothesis', 'backtest_pending', 'validated')
    ),
    parameters_json VARCHAR NOT NULL,
    parameters_hash VARCHAR NOT NULL,
    input_hash VARCHAR NOT NULL,
    provider_id VARCHAR NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    data_through TIMESTAMPTZ,
    state VARCHAR NOT NULL CHECK (
        state IN ('forming', 'confirmed', 'invalidated', 'expired', 'closed')
    ),
    stale BOOLEAN NOT NULL,
    confirmation_blocked BOOLEAN NOT NULL,
    features_json VARCHAR NOT NULL,
    explanation_json VARCHAR NOT NULL,
    entry_json VARCHAR,
    stop_json VARCHAR,
    exit_json VARCHAR,
    reward_to_risk DOUBLE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS strategy_evaluations_latest_idx
    ON strategy_evaluations (symbol, strategy_id, as_of);
