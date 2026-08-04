# Implementation Plan

**Status:** Ready for implementation planning<br>
**Last updated:** 2026-08-03

## Guiding approach

Build the smallest end-to-end research path first, then widen sources and sophistication. Each milestone must leave a runnable, tested system; avoid implementing AI, strategy tuning, and a full dashboard simultaneously.

## Milestone 1 — Project and container foundation

Deliverables:

- Python package structure, dependency lock, configuration schema, and logging.
- FastAPI service with health/readiness endpoints and a minimal dashboard shell.
- Dockerfile and Docker Compose configuration.
- Non-root runtime, loopback-only port, named volume, and database migration mechanism.
- DuckDB connection/service layer and persistent job/run records.

Completion criteria:

- Container builds reproducibly and starts from an empty volume.
- Health checks become ready only after migrations succeed.
- Recreating the container preserves volume state.
- Default service is unreachable through non-loopback host interfaces.

## Milestone 2 — CivicTracker vertical slice

Deliverables:

- `CivicTrackerProvider` using the JSON feed and configured member UUID.
- Pagination, timeouts, retries, polite polling, cursors, deduplication, edits/deletions, and raw hashes.
- Empty-content handling and a fixture-backed `.social-post` HTML fallback parser.
- Source-health and collected-post dashboard views.

Completion criteria:

- Repeating an identical collection creates no duplicate discovery event.
- A content edit updates the stored record and records the change.
- The provider stops paging at a known boundary.
- JSON and fallback parser fixtures normalize into the same canonical schema.

## Milestone 3 — Yahoo market and event data

Deliverables:

- Replaceable `MarketDataProvider`, `NewsProvider`, and `EarningsProvider` interfaces with `yfinance` implementations.
- S&P 500 universe snapshots and SPY benchmark ingestion.
- Batched daily prices, active-candidate intraday prices, corporate actions, news, and earnings data.
- Canonical Parquet datasets, DuckDB metadata, validation/quarantine, caching, and repair provenance.

Completion criteria:

- Repeated runs request only missing data.
- Recorded fixtures reproduce canonical outputs exactly.
- Partial/malformed responses cannot publish fresh signals.
- Daily and intraday timestamps preserve session/timezone semantics.

## Milestone 4 — Company resolution and candidate registry

Deliverables:

- Versioned aliases, deterministic organization extraction, bounded AI extraction fallback, and `yfinance` ticker verification.
- Confidence, alternatives, unresolved/manual-review state, and evidence provenance.
- Candidate union from S&P 500, CivicTracker, earnings events, and general news.
- Expiration/refresh rules so stale leads do not remain active forever.

Completion criteria:

- Known examples such as Chevron resolve to `CVX` with the source passage attached.
- People, states, agencies, and generic industries are rejected.
- Ambiguous company names cannot silently select a ticker.
- AI-returned unknown tickers fail validation.

## Milestone 5 — AI growth analysis

Deliverables:

- Provider-neutral hosted-LLM adapter and encrypted credential storage.
- Evidence-package builder, source deduplication, prompt/schema versions, response validator, and cooldown/cache behavior.
- Growth/evidence scoring, catalysts, risks, bullish thesis, bear case, uncertainty, and source references.
- Assessment history and prospective 5/10/20-session outcome tracking.

Completion criteria:

- Unchanged evidence reuses a cached assessment.
- Unsupported sources, tickers, or claims cause rejection rather than publication.
- Political praise alone cannot qualify a company without other required evidence.
- AI failure leaves deterministic application functions available.

## Milestone 6 — Strategy framework and baseline research

Deliverables:

- Trusted strategy protocol, registry, parameter schemas, market frames, and explanation contract.
- Trend-pullback and breakout baseline implementations.
- Daily trend/relative-strength features and optional completed 15-minute confirmation.
- Setup lifecycle and entry/stop/exit intent records.

Completion criteria:

- Identical data, strategy version, and parameters produce identical signals.
- Strategies cannot access future bars, network, LLM, or arbitrary files.
- Forming and confirmed setups respond correctly to completed-bar updates and stale data.
- No strategy is labeled validated merely because implementation tests pass.

## Milestone 7 — Backtester and research acceptance

Deliverables:

- Event-driven simulated clock, order/fill/position accounting, costs, slippage, stops, targets, trailing/time exits, and SPY benchmark.
- Walk-forward period configuration, parameter experiments, run hashes, and experiment history.
- Metrics for expectancy, returns, payoff, profit factor, drawdown, Sharpe, exposure, turnover, trade count, and regimes.
- Reports that separate deterministic backtests from prospective AI outcomes.

Completion criteria:

- Look-ahead test fixtures fail intentionally flawed strategies.
- Capital, positions, and costs reconcile for every run.
- Same-close execution is impossible for close-derived signals.
- Parameter-stability and out-of-sample reports are produced before a baseline may be enabled for current alerts.

## Milestone 8 — Intraday dashboard and operations

Deliverables:

- Candidate queue, AI evidence, technical setups, alert states, charts, source freshness, and run history.
- Market-calendar-aware schedules for CivicTracker, broad/active news, earnings, price updates, after-close reports, and prospective outcome updates.
- Manual refresh controls with locking and rate protection.
- Encrypted backup/restore, sanitized diagnostics, and optional Windows launcher.

Completion criteria:

- A full market-day simulation exercises ingestion, AI cache behavior, completed-bar signals, stale-data handling, and after-close reconciliation.
- Alerts include data timestamps, thesis, risks, entry zone, invalidation, exits, and reward-to-risk.
- The dashboard can validly display no current setup.
- Backup restores successfully into a clean named volume.

## Test layers

- **Unit:** normalization, hashes, aliases, indicators, strategy states, costs, calendars, and metrics.
- **Fixture integration:** CivicTracker JSON/HTML, Yahoo responses, LLM schemas, and database migrations.
- **Optional live contract:** detect upstream response changes without modifying accepted production history.
- **Property/invariant:** OHLC rules, point-in-time access, capital reconciliation, uniqueness, and idempotency.
- **Security:** secret scans, prompt injection, output escaping, CSRF, loopback binding, and non-root container behavior.
- **Performance:** complete S&P 500 daily ingestion/feature pass and representative backtest within the current 8 GB environment.

## Recommended first build iteration

Use five tickers (`SPY`, `AAPL`, `MSFT`, `CVX`, and one unresolved-name fixture) and one saved CivicTracker fixture. Build this complete path:

```text
CivicTracker JSON
→ deduplicated source item
→ Chevron/CVX resolution
→ mocked structured AI assessment
→ daily price download
→ one simple strategy
→ one deterministic backtest
→ one dashboard result
```

Only after this vertical slice is reliable should collection expand to the full S&P 500 and live hosted-AI calls.

## Decisions intentionally deferred to research

- Which baseline strategy family earns production eligibility.
- Exact moving-average, ATR, volume, holding-period, and reward/risk parameters.
- Minimum sample size and numerical acceptance thresholds.
- Whether intraday confirmation materially improves results after costs.
- Whether a future statistical model improves on simple explainable baselines out of sample.

These are experimental findings, not implementation choices to guess in advance.
