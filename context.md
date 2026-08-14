# Project Context

**Status:** Approved product and architecture direction<br>
**Last updated:** 2026-08-06

## Purpose

Build a private AI-assisted stock-research tool that runs locally, including during market hours. It will discover companies with potential growth catalysts, investigate their news and earnings evidence, and use a deterministic trading algorithm to determine whether a technically valid entry exists.

The tool separates two questions that must not be conflated:

- **Why might this company grow?** Answered by AI analysis of time-stamped news, earnings, estimates, and selected political-policy mentions.
- **Is there a testable trade setup now?** Answered by a versioned price/volume strategy with explicit entry, exit, stop, and invalidation rules.

## Intended user and use

- One local user conducting personal investment research.
- US-listed stocks, initially anchored on the S&P 500 but allowing verified public companies discovered outside it.
- Multi-day swing-trade opportunities, with intraday monitoring used for discovery and entry confirmation.
- Research alerts only; the user remains responsible for every investment decision.

## Version-one workflow

1. Poll CivicTracker's public JSON social-post feed at a polite interval.
2. Collect Yahoo Finance news, earnings calendars, earnings results, estimates, revisions, and market data through `yfinance`.
3. Maintain the S&P 500 as the baseline research universe.
4. Extract company mentions and resolve them to verified tradable tickers.
5. Merge candidates from political mentions, earnings activity, material news, and the S&P 500.
6. Give the AI a bounded, source-attributed evidence package for each candidate.
7. Require the AI to score growth potential, catalysts, evidence quality, uncertainty, and risks.
8. Run AI-qualified candidates through a deterministic technical strategy.
9. Display only qualifying opportunities, including an entry zone, stop/invalidation, target or exit rule, reward-to-risk estimate, and reasoning.
10. Backtest deterministic strategy rules and prospectively track AI-selected candidates.

## Confirmed decisions

- The application will run in Docker and expose a local web dashboard.
- Docker will publish only to `127.0.0.1` by default.
- Persistent state will use a named Docker volume.
- An optional Windows launcher may start Docker Desktop/Compose and open the dashboard.
- `yfinance` is the initial market, news, and earnings-access library.
- CivicTracker data will use its JSON feed first; `.social-post` HTML parsing is fallback behavior only.
- Every external integration is selected by capability through validated configuration. Initial providers are defaults, not dependencies of business logic.
- Changing a packaged provider must require no changes to entity resolution, AI analysis, strategies, backtests, or dashboard code.
- Provider selection is pinned for each run. Provider ID, adapter/model version, capability, schema version, and configuration hash are stored with results so a switch cannot silently alter an existing experiment.
- The CivicTracker member UUID initially monitored is `3094abf7-4a95-4b8d-8c8d-af7d1c3747a1`.
- Previously seen CivicTracker posts must never be reprocessed as new posts.
- AI analysis is source-bounded and must not fabricate unsupported company claims.
- Political mentions are investigation leads, not automatic positive signals.
- RAG and vector embeddings are not part of version one.
- Strategies are trusted, version-controlled Python plugins with validated configuration.
- Historical performance claims apply to deterministic, point-in-time rules.
- AI selections will be saved and evaluated prospectively rather than represented as historically reproduced decisions.
- The system may operate throughout the day but is not designed for execution-critical, low-latency day trading.

## Data and compute constraints

The current environment has a Ryzen 5 3600, 12 logical CPUs, approximately 8 GB RAM, ample disk space, and no GPU currently exposed inside WSL. This is sufficient for ingestion, feature calculations, charts, DuckDB/Parquet analytics, and ordinary backtests. A hosted LLM is the practical default for AI analysis.

`yfinance` is an unofficial open-source client using Yahoo's public interfaces and is intended for personal research use. Its responses must be cached, validated, throttled, and isolated behind a replaceable provider interface. Intraday history available through `yfinance` is limited, so all intraday bars should be archived locally from the first operational day.

## Success criteria

- New source items are collected without duplicating prior work.
- Company names resolve to valid tickers with auditable evidence and confidence.
- Every AI thesis contains catalysts, risks, uncertainty, timestamps, and source references.
- The strategy produces deterministic results from identical data and configuration.
- Backtests prevent look-ahead, include costs, compare against SPY, and disclose survivorship bias.
- The application can validly produce no recommendation when evidence or setup quality is weak.
- A complete deterministic report remains available if the LLM is unavailable.
- A configured provider can be replaced and connection-tested through local settings without rebuilding the image or editing application code.
- No API key, unlock secret, or private configuration appears in source, logs, reports, or container metadata.

## Explicitly out of scope for version one

- Brokerage connectivity and automated orders.
- Short-selling recommendations.
- Personalized portfolio allocation.
- RAG, vector databases, or full-document semantic search.
- LLM fine-tuning or a locally hosted generative model.
- Claims that AI selections have historical performance unless they were actually recorded at that time.
- Guaranteed returns or a forced daily list of recommendations.

## Future possibilities

- Paid, exchange-grade intraday and historical market data.
- Point-in-time historical news and earnings-event datasets.
- A statistical ranking model trained on stored features and forward returns.
- Retrieval of analogous historical cases after sufficient clean records exist.
- SEC filings and earnings transcripts.
- Paper trading, followed only later by separately authorized live execution.
