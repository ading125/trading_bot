# Data Pipeline

**Status:** CivicTracker and Yahoo market/event slices implemented<br>
**Last updated:** 2026-08-20

## CivicTracker collection

Primary route:

```text
GET https://civictracker.us/wp-json/civictracker/v1/proxy/social-posts
    ?limit=20
    &offset=0
    &branch=executive
    &official_uuid=3094abf7-4a95-4b8d-8c8d-af7d1c3747a1
```

The collector uses the JSON response rather than automating a browser. It
retains platform identity, content, UTC publication time, original URL,
media/deletion state, source member, retrieval time, raw-response hash, and
normalization lineage. The upstream CivicTracker row ID is validated but the
platform post ID is the durable identity.

Deduplication and paging rules:

1. Enforce uniqueness on `(platform, post_id)`.
2. Calculate SHA-256 over normalized content and relevant metadata to detect edits.
3. Start at offset zero and page until encountering a stored post boundary or `has_more=false`.
4. Upsert edits and deletion-state changes without treating them as new discovery events.
5. Record empty media-only posts as seen; skip company extraction if no usable text exists.
6. Use a descriptive user agent, timeouts, a 15–30 minute default interval, backoff, and caching.
7. Maintain a BeautifulSoup fallback parser targeting `.social-post`, `.post-content`, `.post-date-bottom`, and original-post URLs, but activate it only when the JSON contract fails validation. CivicTracker's current member page is a JavaScript shell, so this fallback is fixture-verified and will report unavailable unless server-rendered cards exist.

Persistence is in DuckDB tables for current posts, immutable post revisions,
provider/member checkpoints, source health, and per-run collection summaries.
Media-only and deleted posts are retained but marked ineligible for later text
discovery. The collector always starts at offset zero and stops after writing
the known boundary, which lets it detect an edit to that boundary without
re-emitting it as new discovery.

The anonymous public route currently accepts only `offset=0` even though its
first response reports `has_more=true`. The adapter recognizes the route's
explicit `invalid_argument_value` / `Allowed values: 0` response as a clean
end-of-accessible-data boundary. This preserves the newest public page without
misreporting an expected access limit as a collection outage.

## Yahoo Finance collection

The implemented `yfinance==1.6.0` adapter is registered as `yahoo_finance` for
daily/intraday bars, quotes, corporate actions, symbol lookup, news, and
earnings. Production selects it with deterministic fixtures as an explicit
pre-run fallback; tests use recorded providers only.

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

Successful provider payloads are cached before normalization. A validation gate
rejects missing symbols, duplicate timestamps, request mismatches, out-of-range
bars, stale data, and extreme unexplained discontinuities; rejected batches are
stored in `market_quarantine` and never reach canonical datasets. Accepted bars
are revision-tracked in DuckDB and atomically republished as Zstandard-compressed
Parquet partitions by interval, adjustment, symbol, and year. Per-symbol coverage
cursors prevent identical runs from re-requesting already validated ranges.

S&P 500 collection validates the current Wikipedia constituents table, requires
490–520 unique members, hashes the raw page, and stores point-in-time snapshots.
This does not manufacture historical membership before the first snapshot.

## Canonical provider contracts

Adapters normalize bars, corporate actions, symbols, news, earnings, estimates, and social posts into small canonical records. Every record carries the provider ID, provider record/request ID when available, event or publication time, known-available time, retrieval time, raw payload hash/reference, normalization schema version, and adapter version.

Immutable raw responses are retained subject to source terms and retention limits so a corrected adapter can re-normalize old data without re-fetching it. Provider-specific optional fields live in validated extension data and cannot leak into strategy assumptions. Caches, cursors, throttles, and idempotency keys include provider ID plus request identity.

Entity resolution uses a `SymbolLookupProvider`; it does not import `yfinance`. Adding a provider requires an adapter, manifest, canonical mappings, recorded fixtures, and shared contract tests—not changes to entity resolution, scoring, strategy, backtesting, or presentation code.

Price histories from different providers are not appended or compared as if identical. Adjustment, corporate-action, timestamp, and session semantics must pass an explicit reconciliation and lineage job before a dataset can change providers. Existing backtests remain pinned to their original dataset lineage.

## Candidate sources

The candidate union is implemented as an expiring registry. The latest S&P 500
snapshot, canonical news symbols, and canonical earnings symbols enter directly
with source attribution. CivicTracker text enters only after deterministic
extraction and verified resolution. A refresh reconciles removed/edited sources,
deactivates unseen or expired evidence, and recomputes active candidates.

Candidate membership is a union with source attribution:

- Current S&P 500 universe.
- Verified public companies extracted from CivicTracker posts.
- Companies with recent or imminent material earnings events.
- Companies associated with current material general news.

Membership alone is not an endorsement. Each candidate carries source type, source record IDs, first/last seen timestamps, extraction method, relevance, and expiration.

## Company extraction and ticker resolution

The implemented resolver loads packaged `company_aliases.v1`, augments it with
each point-in-time S&P 500 snapshot, matches longest aliases and explicit ticker
syntax, and recognizes names with legal company suffixes. Unknown names query the
configured `SymbolLookupProvider`. Low-confidence and closely tied alternatives
remain unresolved or ambiguous and never silently produce a candidate.

The bounded AI-extraction interface is present but intentionally uses a no-op
implementation until Milestone 6 configures a hosted LLM. Any future suggestions
must name text present in the source, classify the entity, stay within source and
suggestion caps, and pass the same independent symbol-provider verification.

Resolution pipeline:

1. Normalize source text without removing material qualifiers.
2. Extract possible organization/company names using deterministic aliases plus bounded AI/entity extraction.
3. Reject obvious people, places, agencies, political bodies, and generic industries.
4. Resolve known aliases from a versioned registry.
5. Query the configured `SymbolLookupProvider` for unresolved names (`yfinance` initially).
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
- Provider IDs, adapter versions, schema versions, capabilities, and failover events are part of dataset and run provenance.
