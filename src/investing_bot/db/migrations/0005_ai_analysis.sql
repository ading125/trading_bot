CREATE TABLE IF NOT EXISTS analysis_evidence_packages (
    evidence_hash VARCHAR PRIMARY KEY,
    ticker VARCHAR NOT NULL,
    company_name VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    output_schema_version VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_assessments (
    assessment_id VARCHAR PRIMARY KEY,
    cache_key VARCHAR NOT NULL UNIQUE,
    evidence_hash VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    company_name VARCHAR NOT NULL,
    decision VARCHAR NOT NULL,
    growth_score INTEGER NOT NULL CHECK (growth_score BETWEEN 0 AND 100),
    evidence_quality INTEGER NOT NULL CHECK (evidence_quality BETWEEN 0 AND 100),
    policy_relevance VARCHAR NOT NULL,
    catalysts_json VARCHAR NOT NULL,
    earnings_assessment VARCHAR NOT NULL,
    bullish_thesis VARCHAR NOT NULL,
    bearish_case VARCHAR NOT NULL,
    risks_json VARCHAR NOT NULL,
    uncertainties_json VARCHAR NOT NULL,
    source_ids_json VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    output_schema_version VARCHAR NOT NULL,
    provider_id VARCHAR NOT NULL,
    adapter_version VARCHAR NOT NULL,
    provider_request_id VARCHAR NOT NULL,
    configuration_hash VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ai_assessments_ticker_created_idx
    ON ai_assessments (ticker, created_at);

CREATE TABLE IF NOT EXISTS analysis_outcomes (
    assessment_id VARCHAR NOT NULL,
    horizon_sessions INTEGER NOT NULL CHECK (horizon_sessions IN (5, 10, 20)),
    baseline_session_date DATE,
    baseline_close DOUBLE,
    outcome_session_date DATE,
    outcome_close DOUBLE,
    return_pct DOUBLE,
    recorded_at TIMESTAMPTZ,
    PRIMARY KEY (assessment_id, horizon_sessions)
);
