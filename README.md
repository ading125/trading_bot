# Investing Bot

**Status:** Milestones 1–7 implemented; Milestone 8 backtesting is next<br>
**Last updated:** 2026-08-21

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
- [Provider framework](docs/PROVIDER_FRAMEWORK.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

## Current technology direction

- Python 3.12
- FastAPI local service
- Jinja/HTMX dashboard with Plotly charts
- DuckDB for application records and Parquet for market history
- `yfinance` for initial Yahoo Finance data access
- CivicTracker's public JSON feed for selected executive social posts
- BeautifulSoup HTML parsing as a schema-validated CivicTracker fallback
- Provider-neutral hosted LLM adapter
- Per-capability provider registry and local settings so data and AI APIs can be changed without business-logic edits or an image rebuild
- Docker Compose with named-volume persistence and loopback-only networking

See [Project outline](docs/PROJECT_OUTLINE.md) for the complete flow and [Implementation plan](docs/IMPLEMENTATION_PLAN.md) for the recommended build order.

## Local development

Install the exact development dependency set and run the test suite:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m pytest
```

Run the service directly with a writable local data directory:

```bash
INVESTING_BOT_DATA_DIR="$PWD/data" .venv/bin/python -m investing_bot
```

When Docker is available, build and run the hardened local container:

```bash
docker compose up --build
```

The dashboard will be available at `http://127.0.0.1:8000`. Never place API
keys in `.env`; use the encrypted credential-vault commands described below.

Credential secrets are entered only through hidden interactive-terminal prompts:

```bash
INVESTING_BOT_DATA_DIR="$PWD/data" .venv/bin/python -m investing_bot credentials init
INVESTING_BOT_DATA_DIR="$PWD/data" .venv/bin/python -m investing_bot credentials set cred_groq
INVESTING_BOT_DATA_DIR="$PWD/data" .venv/bin/python -m investing_bot credentials status
INVESTING_BOT_DATA_DIR="$PWD/data" .venv/bin/python -m investing_bot serve --unlock-credentials
```

The existing `python -m investing_bot` start command remains unchanged and
starts with the vault locked. In that mode, live Groq analysis is unavailable
and the analysis capability safely falls back to the recorded fixture. Vault
files live under `data/credentials` with
owner-only permissions. References, initialization state, and locked/unlocked
state are safe to inspect; decrypted values are never returned by the API.

Production/development mode selects live CivicTracker JSON for social posts and
falls back to its HTML adapter if a compatible server-rendered page is available.
Tests remain fully offline on deterministic recorded providers. Copy
[providers.example.json](providers.example.json) to `data/providers.json` only
when you want to override the default selections.

The live CivicTracker collector begins in the background, polls no more often than every 15
minutes by default, and fetches at most five pages per run. Its member UUID,
interval, page size, page cap, timeout, retry count, and enabled state are
non-secret `INVESTING_BOT_CIVICTRACKER_*` settings.

Market collection uses live Yahoo Finance providers outside test mode but is
opt-in by default. To enable bounded hourly collection for the configured seed
watchlist, set `INVESTING_BOT_MARKET_COLLECTION_ENABLED=true` and restart the
service. Canonical bars are written below `data/market`, raw Yahoo payloads below
`data/cache/yahoo`, and the dashboard/API expose dataset and quarantine counts.
The watchlist and polling/history bounds use the non-secret
`INVESTING_BOT_MARKET_*` settings in [.env.example](.env.example).

Candidate refresh runs every 15 minutes by default and uses stored source data.
It creates expiring, source-attributed research leads from the latest S&P 500
snapshot, CivicTracker mentions, news, and earnings. View them at
`/api/v1/candidates`, with exact source passages under each ticker's
`/api/v1/candidates/{symbol}/evidence` route. Ambiguous and unresolved mentions
remain visible at `/api/v1/resolutions` but cannot enter the candidate list.

Milestone 6 analyzes the bounded `CVX` seed with Groq's fixed
`openai/gpt-oss-120b` model whenever the vault is explicitly unlocked. It stores
immutable evidence packages,
provider-version-aware cache keys, assessment history, and prospective 5/10/20
session outcome slots. The dashboard shows the latest thesis, scores, catalysts,
bear case, risks, uncertainty, and evidence link. Read-only records are also
available at `/api/v1/analyses`, `/api/v1/analyses/{symbol}`, and
`/api/v1/analyses/{symbol}/evidence`. Every assessment records the actual
provider, model, adapter, request ID, configuration, and input/output token
counts. Requests use strict JSON Schema output, bounded source evidence, citation
validation, sanitized errors, and the recorded provider as a pre-run fallback.

Milestone 7 evaluates only active candidates whose latest AI decision is
`qualify`. Two trusted, source-controlled strategy hypotheses—trend pullback and
range breakout—consume immutable, provider-consistent, completed market frames.
They produce forming, confirmed, or invalidated setup records with theoretical
entry zones, stops, exit rules, reward-to-risk, calculated features, and
machine-readable explanations. Evaluations and history are available at
`/api/v1/strategies`, `/api/v1/setups`, and
`/api/v1/setups/{symbol}/history`. The dashboard labels all output as hypothesis
research; live alerts remain disabled until Milestone 8 backtesting and
acceptance checks pass.

Verification: 129 offline tests cover provider contracts, CivicTracker
collection, Yahoo normalization, incremental market coverage,
validation/quarantine, revision history, actual Parquet publication, deterministic
company resolution, ambiguity/manual-review behavior, source provenance, and
candidate expiry, source-bounded analysis validation, caching, history, and
political-only qualification rejection, credential encryption, wrong-secret and
tamper rejection, locked-state isolation, unsafe-permission rejection, strict
Groq request/response handling, token lineage, sanitized provider failures,
strategy determinism, no-look-ahead/provider-isolation invariants, stale-state
handling, evaluation caching, and nested setup persistence. Live
SPY and Chevron Yahoo checks and
the local fixture-backed CVX analysis were smoke-tested successfully on
2026-08-20.
