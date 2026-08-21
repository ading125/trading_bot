CREATE TABLE IF NOT EXISTS backtest_runs (
    run_hash VARCHAR PRIMARY KEY,
    strategy_id VARCHAR NOT NULL,
    strategy_version VARCHAR NOT NULL,
    provider_id VARCHAR NOT NULL,
    period_name VARCHAR NOT NULL CHECK (
        period_name IN ('development', 'validation', 'out_of_sample')
    ),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    parameters_hash VARCHAR NOT NULL,
    data_hash VARCHAR NOT NULL,
    request_json VARCHAR NOT NULL,
    result_json VARCHAR NOT NULL,
    total_return_pct DOUBLE NOT NULL,
    maximum_drawdown_pct DOUBLE NOT NULL,
    sharpe_ratio DOUBLE,
    trade_count INTEGER NOT NULL,
    benchmark_return_pct DOUBLE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS backtest_runs_latest_idx
    ON backtest_runs (strategy_id, created_at);

CREATE TABLE IF NOT EXISTS backtest_experiments (
    experiment_hash VARCHAR PRIMARY KEY,
    strategy_id VARCHAR NOT NULL,
    strategy_version VARCHAR NOT NULL,
    provider_id VARCHAR NOT NULL,
    selected_candidate_id VARCHAR NOT NULL,
    acceptance_passed BOOLEAN NOT NULL,
    request_json VARCHAR NOT NULL,
    report_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS backtest_experiments_latest_idx
    ON backtest_experiments (strategy_id, created_at);
