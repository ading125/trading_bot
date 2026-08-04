# Investing Bot

**Status:** Planning<br>
**Last updated:** 2026-08-03

Investing Bot is a private, locally hosted stock-research tool. It discovers public companies from current news, earnings activity, the S&P 500, and selected political-policy sources; uses an AI model to evaluate growth potential; and passes qualified companies to a deterministic, backtested strategy that calculates potential entries, exits, and invalidation levels.

The application is research-only. It will not place orders, manage a brokerage account, guarantee returns, or treat a political mention as an automatic bullish signal.

## Version-one outcome

A local Docker dashboard will:

1. Collect and deduplicate source material throughout the day.
2. Resolve company mentions to verified public tickers.
3. Evaluate news and earnings evidence with a hosted AI model.
4. Apply a separately versioned technical strategy to qualified candidates.
5. Present only candidates that satisfy minimum evidence, data-quality, and technical-entry requirements.
6. Backtest deterministic strategy rules with point-in-time historical prices.
7. Preserve every AI assessment and live signal for prospective performance tracking.

RAG, a vector database, local LLM training, live order execution, short selling, and latency-sensitive day trading are not version-one requirements.

## Documentation

- [Project context](context.md)
- [Decision log](chat.md)
- [Project outline](docs/PROJECT_OUTLINE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Data pipeline](docs/DATA_PIPELINE.md)
- [AI analysis](docs/AI_ANALYSIS.md)
- [Strategy and backtesting](docs/STRATEGY_AND_BACKTESTING.md)
- [Security](docs/SECURITY.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

## Current technology direction

- Python 3.12
- FastAPI local service
- Jinja/HTMX dashboard with Plotly charts
- DuckDB for application records and Parquet for market history
- `yfinance` for initial Yahoo Finance data access
- CivicTracker's public JSON feed for selected executive social posts
- Provider-neutral hosted LLM adapter
- Docker Compose with named-volume persistence and loopback-only networking

See [Project outline](docs/PROJECT_OUTLINE.md) for the complete flow and [Implementation plan](docs/IMPLEMENTATION_PLAN.md) for the recommended build order.
