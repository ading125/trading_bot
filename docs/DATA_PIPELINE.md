# Data Pipeline

**Status:** Version-one data contract<br>
**Last updated:** 2026-08-03

## CivicTracker collection

Primary route:

```text
GET https://civictracker.us/wp-json/civictracker/v1/proxy/social-posts
    ?limit=20
    &offset=0
    &branch=executive
    &official_uuid=3094abf7-4a95-4b8d-8c8d-af7d1c3747a1
```

The collector will use the JSON response rather than automate a browser. It will retain CivicTracker ID, platform, platform post ID, content, UTC publication time, original URL, media indicator/metadata, deletion state, source member, retrieval time, and raw-response hash.

Deduplication and paging rules:

1. Enforce uniqueness on `(platform, post_id)`.
2. Calculate SHA-256 over normalized content and relevant metadata to detect edits.
3. Start at offset zero and page until encountering a stored post boundary or `has_more=false`.
4. Upsert edits and deletion-state changes without treating them as new discovery events.
5. Record empty media-only posts as seen; skip company extraction if no usable text exists.
6. Use a descriptive user agent, timeouts, a 15–30 minute default interval, backoff, and caching.
7. Maintain a BeautifulSoup fallback parser targeting `.social-post`, `.post-content`, `.post-date-bottom`, and original-post URLs, but activate it only when the JSON contract fails validation.

## Yahoo Finance collection

Use a pinned `yfinance` version behind adapter interfaces.

Collect:

- Adjusted and raw daily OHLCV, dividends, and splits.
- Intraday OHLCV for the active candidate set.
- Company/ticker lookup data.
- Current company news and press releases where available.
- Earnings calendar, reported versus estimated EPS, earnings history, estimate/revision data, and selected growth measures.
- SPY data as the benchmark and relative-strength reference.

Operational rules:

- Batch price downloads and request only missing time ranges.
- Cache successful responses before processing.
- Use bounded concurrency and exponential backoff.
- Store timezone-aware UTC timestamps plus exchange/session metadata.
- Reject duplicate timestamps, negative volume, impossible OHLC relationships, stale final bars, and unexplained extreme discontinuities pending review.
- Store whether `repair=True` was used and never silently overwrite accepted history with repaired values.
- Archive intraday bars locally from day one because Yahoo intraday retrieval does not provide unlimited history.

## Candidate sources

Candidate membership is a union with source attribution:

- Current S&P 500 universe.
- Verified public companies extracted from CivicTracker posts.
- Companies with recent or imminent material earnings events.
- Companies associated with current material general news.

Membership alone is not an endorsement. Each candidate carries source type, source record IDs, first/last seen timestamps, extraction method, relevance, and expiration.

## Company extraction and ticker resolution

Resolution pipeline:

1. Normalize source text without removing material qualifiers.
2. Extract possible organization/company names using deterministic aliases plus bounded AI/entity extraction.
3. Reject obvious people, places, agencies, political bodies, and generic industries.
4. Resolve known aliases from a versioned registry.
5. Query `yfinance` Search/Lookup for unresolved names.
6. Require a US-listed equity match and validate exchange, quote type, and active status.
7. Store confidence and alternatives; route ambiguity below threshold to manual review.

AI output cannot directly create a ticker without resolver verification.

## Canonical storage

DuckDB tables should cover:

- Source items and raw response metadata.
- CivicTracker posts and collection cursors.
- News/earnings records.
- Company aliases, entities, ticker resolutions, and candidate membership.
- AI evidence packages and assessments.
- Strategy versions, parameter sets, signals, alerts, and prospective outcomes.
- Backtest runs, orders, fills, positions, equity curves, and metrics.
- Job runs, source health, settings metadata, and audit events.

Partitioned Parquet datasets should store canonical daily/intraday bars and derived feature frames. Partition keys should support efficient symbol/date reads without producing excessive tiny files.

## Point-in-time integrity

- Every record needs both event/publication time and retrieval time.
- Backtests may use a record only after its known-available timestamp.
- Signals calculated from a bar close execute no earlier than the next permitted simulated price.
- Earnings revisions and news without reliable historical availability are excluded from historical strategy tests.
- S&P 500 membership snapshots start when collection begins; older tests using today's constituents must be labeled survivorship-biased.
- Raw source hashes, dataset versions, code commits, configuration hashes, and run IDs make results reproducible.
