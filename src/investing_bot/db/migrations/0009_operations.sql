ALTER TABLE analysis_outcomes
    ADD COLUMN IF NOT EXISTS market_provider_id VARCHAR;

CREATE TABLE IF NOT EXISTS operational_schedule_runs (
    task VARCHAR NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    error_summary VARCHAR,
    PRIMARY KEY (task, scheduled_for)
);

CREATE INDEX IF NOT EXISTS idx_operational_schedule_runs_started
    ON operational_schedule_runs (started_at DESC);
