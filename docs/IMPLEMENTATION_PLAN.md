# Implementation Plan

**Status:** Milestones 1–9 implemented<br>
**Last updated:** 2026-08-21

## Guiding approach

Build the smallest end-to-end research path first, then widen sources and sophistication. Each milestone must leave a runnable, tested system; avoid implementing AI, strategy tuning, and a full dashboard simultaneously.

## Milestone 1 — Project and container foundation

Implementation status (2026-08-14): application code, migrations, persistent
job/run records and leases, dependency locks, health/readiness routes, dashboard
shell, Dockerfile, Compose hardening, and automated foundation tests are in
place. The local environment does not provide Docker, so image build, clean
volume startup, volume recreation, and host-network reachability still require
runtime verification before this milestone is marked complete.

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

## Milestone 2 — Provider framework

Implementation status (2026-08-17): capability-specific protocols, canonical
records and provenance, manifests, the packaged registry, local validated
selection, opaque credential references, connection health, run pinning,
controlled fallback, recorded fixture adapters, read-only provider API/views,
and shared contract/architecture tests are implemented. Live providers and
encrypted credential storage remain assigned to their later milestones.

Deliverables:

- Narrow provider protocols, canonical request/response/error models, `ProviderRegistry`, and `ProviderManifest`.
- Validated per-capability selection, encrypted credential references, capability discovery, connection tests, health status, and controlled fallback.
- Recorded fixture providers for market data, symbol lookup, news, earnings, social posts, and structured LLM analysis.
- Shared adapter contract tests for schema/error mapping, timezone/session and adjustment semantics, pagination/idempotency, quotas/rate limits, and structured AI output.

Completion criteria:

- A fixture provider can replace another through configuration alone; business services, strategies, backtests, and dashboard views remain unchanged.
- Provider choice is pinned within a run and all records preserve provider, adapter, schema, dataset-lineage, and fallback provenance.
- An unsupported or unhealthy capability degrades explicitly without disabling unrelated deterministic functions.
- Provider SDK imports outside adapter packages fail an architectural test.

## Milestone 3 — CivicTracker vertical slice

**Status: implemented and verified on 2026-08-19.**

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

Verification: 97 automated tests cover the live-adapter contract, polite
headers/retry behavior, JSON/HTML parity, durable deduplication, edit/deletion
revisions, and known-boundary pagination. A live endpoint smoke test remains a
runtime check because the test suite intentionally has no network dependency.

## Milestone 4 — Yahoo market and event data

**Status:** Implemented and fixture/live-smoke verified on 2026-08-20.

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

## Milestone 5 — Company resolution and candidate registry

**Status:** Implemented and fixture/live-smoke verified on 2026-08-20.

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

## Milestone 6 — AI growth analysis

Implementation status: the vertical slice is complete with both deterministic
recorded analysis and a live Groq adapter for `openai/gpt-oss-120b`. Evidence
packages, strict structured output, local citation validation, model-aware cache
and history persistence, token usage, prospective outcome slots, APIs,
background scheduling, and dashboard presentation are implemented. The
authenticated encrypted vault, terminal-only credential entry, explicit unlock,
provider-store injection, sanitized status reporting, and recorded-provider
fallback are also implemented.

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

## Milestone 7 — Strategy framework and baseline research

**Implementation status:** Complete as of 2026-08-21. The baselines remain
unvalidated hypotheses by design; research acceptance belongs to Milestone 8.

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

## Milestone 8 — Backtester and research acceptance

**Implementation status:** Complete as of 2026-08-21. No baseline passed merely
because the engine was implemented; alert eligibility requires a persisted
walk-forward acceptance report and a separate source-controlled review.

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

## Milestone 9 — Intraday dashboard and operations

**Implementation status:** Complete as of 2026-08-21. Production polling is
owned by a persisted, market-calendar-aware scheduler; manual controls use a
separate audited locking/cooldown path.

Deliverables:

- Candidate queue, AI evidence, technical setups, alert states, charts, source freshness, and run history.
- Provider settings and health view showing selection, supported capabilities, last success, freshness, latency, sanitized quota state, schema/contract status, and fallback activity.
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
- **Fixture integration:** CivicTracker JSON/HTML, Yahoo responses, LLM schemas, provider contract suites, provider-switch lineage, raw-payload re-normalization, and database migrations.
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
