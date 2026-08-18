CREATE TABLE IF NOT EXISTS job_runs (
    run_id VARCHAR PRIMARY KEY,
    job_type VARCHAR NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'interrupted')
    ),
    code_version VARCHAR NOT NULL,
    config_hash VARCHAR NOT NULL,
    error_summary VARCHAR,
    lease_owner VARCHAR,
    lease_expires_at TIMESTAMPTZ,
    CHECK (finished_at IS NULL OR started_at IS NOT NULL),
    CHECK (status NOT IN ('succeeded', 'failed', 'interrupted') OR finished_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS job_runs_type_requested_idx
    ON job_runs (job_type, requested_at);

CREATE TABLE IF NOT EXISTS job_leases (
    job_type VARCHAR PRIMARY KEY,
    lease_owner VARCHAR NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    CHECK (expires_at > acquired_at)
);
