# AI Working Prompt and Decision Log

**Status:** Active<br>
**Last updated:** 2026-08-17

## AI Working Prompt

You are the senior AI collaborator for Investing Bot. Act as a top-level stock trader, quantitative researcher, financial-data engineer, and application architect. Your mission is to help develop the best possible private investing-research application using the full extent of your relevant knowledge, careful reasoning, and available tools.

### Subagent Delegation

For every distinct project task, spawn at least one fresh subagent before beginning substantive work. For broad implementation work, assign a fresh subagent to each clearly separable file, external integration, or architectural aspect. Give every subagent a concrete, bounded objective, explicit and non-overlapping ownership, relevant repository paths, applicable project decisions, constraints, expected deliverables, and verification criteria.

Schedule independent assignments in bounded waves that respect the available concurrency limit. Integrate and review one wave before dispatching dependent work so that later agents receive the current cross-file contracts and repository state. Trivial, tightly coupled files may be grouped under one subagent when separate ownership would create artificial boundaries, duplicated work, or poorer coherence. Do not create vague, overlapping, recursive, or unbounded delegation chains.

The primary agent remains responsible for defining and preserving cross-file contracts, coordinating the work, resolving conflicts, reviewing all returned results, integrating changes, and performing final end-to-end verification. It also retains direct responsibility for security review and for explaining outcomes, important decisions, verification evidence, and the AI workflow to the user. Do not turn implementation updates into coding lessons unless the user asks. Never accept a subagent's conclusions or edits without checking them against `context.md`, this decision log, the implementation documents, the current repository state, and the user's latest instructions.

Subagents should not spawn additional agents unless explicitly requested by the user or required for a clearly separable subtask, and delegation depth must remain bounded. If subagents are unavailable, fail to complete the task, or return inadequate work, continue directly when safe; otherwise report the precise blocker. Delegation never transfers responsibility for correctness, security, testing, or the final response.

### Clarification and Design Collaboration

Ask the user focused clarification questions whenever a genuine product, design, workflow, risk, or implementation tradeoff could materially change the result. Questions are encouraged when they clarify success criteria, expose meaningful alternatives, or prevent rework. The user is not expected to anticipate every design decision or know the exact question to ask.

Before asking, resolve discoverable facts by inspecting the code, configuration, documentation, tests, and current project state. Do not ask where something is or how it currently works when the repository can answer it.

When ambiguity is low-risk and does not change product intent, security, data integrity, financial interpretation, or public interfaces, proceed with a reasonable documented assumption and state it clearly. Do not block useful progress on minor preferences. When a choice materially affects scope, architecture, workflow, financial behavior, security, external side effects, or difficult-to-reverse work, pause and ask before committing to it.

Keep questions concise, explain why the answer matters, identify the meaningful alternatives, and recommend a default when practical. Continue safe, non-blocked work while awaiting answers when possible. After the user settles a decision, update `context.md`, this decision log, and every affected specification document so later agents do not need to ask the same question again.

Approach the project like an expert responsible for both investment quality and engineering correctness:

- Search for genuine, repeatable market edge rather than producing exciting but unsupported recommendations.
- Use current news, earnings, estimates, company evidence, political-policy developments, price, volume, volatility, and market regime when those inputs are available and permitted.
- Treat CivicTracker and political mentions as investigation leads, never automatic bullish or bearish signals.
- Separate the qualitative growth thesis from the deterministic entry/exit strategy.
- Require every actionable setup to include its evidence, entry conditions, invalidation/stop, exit rules, expected reward-to-risk, risks, uncertainty, and data timestamp.
- Prefer no recommendation when evidence or setup quality is inadequate. Never force a daily pick or a top percentage from a weak candidate set.
- Never invent prices, tickers, earnings figures, news, citations, backtest results, or source content. Clearly identify missing, stale, conflicting, or uncertain data.
- Evaluate strategies by expectancy, payoff ratio, costs, drawdown, stability, sample size, and out-of-sample results—not win rate alone.
- Prevent look-ahead bias, survivorship bias, data leakage, same-bar execution errors, overfitting, and selective reporting of favorable experiments.
- Keep AI-generated historical claims separate from deterministic backtests. Track live AI assessments prospectively unless true point-in-time historical evidence exists.
- Protect credentials and private data. Do not expose secrets through source code, prompts, logs, reports, environment metadata, or container configuration.
- Keep the application modular, testable, reproducible, locally deployable, and operable in Docker within the documented hardware constraints.
- Challenge weak assumptions constructively and recommend simpler or safer designs when they produce more trustworthy results.
- Maintain the project documentation as decisions change; do not silently revive removed requirements such as version-one RAG or live order execution.

Before proposing or implementing project changes, read [context.md](context.md) and the documents linked from [README.md](README.md). Treat this prompt as the operating role, this file's decision log as project history, `context.md` as the current product source of truth, and the detailed files under `docs/` as the implementation specification. Newer explicit user decisions take precedence and must be reflected consistently across the documentation.

The application is a research tool, not a guarantee of returns. Preserve the user's control over every real investment decision and do not add brokerage execution without separate, explicit authorization.

## Decision Log

The following sections record settled decisions and their reasoning. They are not a verbatim conversation transcript.

## 2026-08-02 — Initial product direction

- Selected an end-of-day stock-research assistant rather than an automated trading bot.
- Chose multi-day swing trades as the initial holding horizon.
- Selected the S&P 500 as the baseline universe.
- Kept version one research-only, with no paper or live order submission.
- Chose an explainable strategy direction instead of immediately training a predictive ML model.
- Defined negative output as **avoid/underweight**, not a short recommendation.
- Allowed public market information to be sent to a hosted LLM while keeping credentials and private data local.
- Preferred free data sources initially.

## 2026-08-02 — Local application and Docker

- Initially considered a native desktop interface, then chose a localhost web dashboard because the complete application must also run in Docker.
- Selected a Docker container for the service, ingestion jobs, scheduler, database, and dashboard.
- Selected a named Docker volume for persistence.
- Retained an optional Windows launcher to start Docker Compose and open the local dashboard.
- Required loopback-only port publishing and non-root container execution.

## 2026-08-02 — Yahoo Finance provider

- Selected the Python `yfinance` library as the initial market-data adapter.
- Kept it behind a replaceable interface because it is not an official contracted Yahoo API SDK.
- Required caching, throttling, retries, validation, corporate-action handling, and pinned dependency versions.

## 2026-08-03 — AI and strategy separation

- Clarified that AI should discover and assess stocks using news and earnings evidence.
- Clarified that a separate algorithm will determine entries and exits.
- Required deterministic strategy rules to be backtested.
- Rejected historical performance claims for a hosted LLM unless the exact decision was actually made and stored at the historical time.
- Chose to save AI picks and evaluate their returns prospectively.
- Decided that win rate alone is insufficient; strategy evaluation must include expectancy, payoff ratio, costs, drawdown, and out-of-sample stability.

## 2026-08-03 — CivicTracker ingestion

- Added CivicTracker executive social posts as a candidate-discovery source.
- Initial page: `https://civictracker.us/executive/member/?uuid=3094abf7-4a95-4b8d-8c8d-af7d1c3747a1`.
- Live inspection showed that the page calls a public JSON route under `/wp-json/civictracker/v1/proxy/social-posts`.
- Selected the JSON feed as the primary integration instead of scraping rendered `.social-post` nodes.
- Kept a BeautifulSoup `.social-post` parser as a fallback if the feed changes.
- Required deduplication by `(platform, post_id)` plus a normalized-content hash.
- Required empty media-only posts to be recorded as seen and skipped unless usable metadata becomes available.
- Established that company mentions create investigation leads. Political praise, criticism, or policy language cannot directly produce a buy signal.

## 2026-08-03 — Intraday operation

- The application may run throughout the market day.
- CivicTracker and news collection will run periodically, while technical rules evaluate completed bars rather than incomplete price intervals.
- Intraday monitoring supports discovery and swing-trade entry confirmation; it is not a low-latency execution platform.
- Active candidates may receive more frequent refreshes than the full S&P 500 universe.
- All available intraday bars will be archived locally because `yfinance` does not provide unlimited intraday history.

## 2026-08-03 — RAG removed

- Removed RAG, vector databases, embeddings, and document chunking from version one.
- The AI will receive a curated structured evidence package containing only relevant current news, earnings information, political-policy mentions, and computed market features.
- If the project later accumulates enough clean historical cases, analogous-case retrieval may be reconsidered, but it is not required for the product to succeed.

## 2026-08-06 — Provider switching

- Required every external API to be replaceable per capability through validated local configuration, without editing business logic or rebuilding the image.
- Kept `yfinance` and CivicTracker JSON as initial adapters rather than permanent dependencies.
- Required narrow provider contracts, a packaged provider registry, capability discovery, connection tests, normalized records, provider-scoped encrypted credentials, and shared contract tests.
- Prohibited provider SDK imports in entity resolution, evidence building, AI analysis, strategy, backtesting, and presentation layers.
- Required provider selection to remain fixed within a run and every result to retain the actual provider, adapter/model version, schema version, and failover history.
- Allowed ordered fallbacks for explicitly compatible capabilities, but prohibited silent mid-run switching or untracked blending of datasets from different providers.

## 2026-08-07 — File and integration delegation

- Required broad project work to assign a fresh subagent to each clearly separable file, external integration, or architectural aspect.
- Required assignments to have bounded scope and non-overlapping ownership, with independent work dispatched in waves that respect the available concurrency limit.
- Preserved the requirement to use at least one fresh subagent for every distinct project task.
- Allowed trivial, tightly coupled files to be grouped when splitting them would harm coherence or create unnecessary coordination overhead.
- Kept the primary agent responsible for cross-file contracts, security, teaching explanations, integration, review of subagent work, and final end-to-end verification.

## 2026-08-14 — AI-workflow learning focus

- The user no longer intends to use this project to learn how to code.
- Project updates should focus on outcomes, decisions, risks, verification, and how to direct and review an AI development workflow.
- Provide code-level teaching only when the user explicitly requests it.

## 2026-08-17 — Provider framework implementation

- Implemented capability-specific provider contracts and an explicit packaged registry.
- Selected providers are pinned before a run begins; fallback attempts are recorded, and silent mid-run switching is prohibited.
- Local configuration stores only opaque credential references, never usable secrets.
- Deterministic recorded fixture providers are the default until live adapters arrive in later milestones.
- Optional provider health is reported per capability and does not disable unrelated deterministic functions.

## 2026-08-19 — CivicTracker vertical slice implementation

- Implemented the verified public JSON contract with a descriptive user agent,
  member-page referrer, timeout, bounded exponential backoff with jitter, and
  offset pagination.
- Made `(platform, post_id)` the durable identity; raw-payload hashes preserve
  source-change evidence while normalized content hashes drive edit detection.
- Persisted current posts and immutable revisions separately so edits and
  deletions update current state without becoming duplicate discovery events.
- Chose to start each poll at offset zero and stop at the prior newest boundary;
  the boundary itself is still written so an edit to it cannot be missed.
- Kept media-only and deleted posts as seen records while excluding them from
  later text discovery.
- Registered HTML as a validated fallback but treated absence of server-rendered
  `.social-post` cards as explicit unavailability; the current live page is a
  JavaScript shell.
- Enabled live social collection only outside test mode. Automated tests stay
  deterministic and offline; production polling defaults to 15 minutes and a
  five-page safety cap.
- Confirmed the anonymous route currently restricts `offset` to zero despite
  reporting `has_more=true`; treat that exact upstream response as the end of
  accessible public data rather than a failed run.

## 2026-08-20 — Yahoo market and event data implementation

- Pinned `yfinance==1.6.0` behind the existing market/news/earnings provider
  contracts; downstream services remain unaware of Yahoo response shapes.
- Added point-in-time S&P 500 universe snapshots plus Yahoo symbol translation
  for share classes such as canonical `BRK.B` versus Yahoo `BRK-B`.
- Chose DuckDB as the canonical catalog and revision ledger while publishing
  validated OHLCV history to atomic, Zstandard-compressed Parquet partitions.
- Added raw response caching, repair lineage, bounded retries/concurrency, daily
  session semantics, completed 15-minute bars, quotes, corporate actions, news,
  and earnings normalization.
- Required whole-request validation before publication. Missing/partial,
  duplicate, stale, out-of-range, or suspiciously discontinuous batches enter
  quarantine and cannot replace accepted market history.
- Added validated-through coverage cursors, separate from last observed bar, so
  repeat runs do not repeatedly request weekend/holiday tails.
- Kept broad live market polling opt-in. The initial enabled scope is a bounded,
  configurable seed watchlist including SPY; this avoids an unreviewed 500-symbol
  background load while preserving the full universe snapshot.
- Added read-only market status, bar, and dataset APIs plus dashboard counts.
- Verified 103 offline tests and a successful live SPY Yahoo health probe.

## 2026-08-20 — Company resolution and candidate registry implementation

- Added a packaged, versioned company-alias registry and augmented it with each
  point-in-time S&P 500 snapshot rather than hard-coding the current universe.
- Chose deterministic longest-alias, explicit-ticker, and legal-company-suffix
  extraction as the first pass. The bounded AI extraction interface is present
  but remains a no-op until the hosted-LLM milestone.
- Required every unresolved name or suggested ticker to pass the configured
  `SymbolLookupProvider`; neither source text nor future AI output may directly
  manufacture a tradable symbol.
- Made ambiguity a durable manual-review state. Close provider alternatives or
  aliases mapping to multiple tickers cannot silently select a candidate.
- Rejected suggestions classified as people, places, agencies, industries, or
  ungrounded names before ticker lookup.
- Implemented an expiring candidate union across the latest S&P 500 snapshot,
  CivicTracker passages, canonical news, and canonical earnings records.
- Stored exact source excerpts, source IDs/URLs, event and observation times,
  extraction method, resolution identity, relevance, and expiration for every
  candidate-evidence link.
- Reconciled edited/deleted sources immediately and deactivated evidence that is
  no longer present, instead of waiting only for its time-based expiry.
- Added read-only candidate, evidence, and resolution APIs plus dashboard status.
- Verified 108 offline tests and a successful live Chevron-to-CVX Yahoo lookup.

## 2026-08-20 — Fixture-backed AI growth-analysis vertical slice

- Added migration 5 for immutable evidence packages, assessment history,
  provider/configuration lineage, provider-aware cache keys, and prospective
  5/10/20-session outcome records.
- Bounded analysis inputs to active, source-attributed candidate evidence;
  deduplicated repeated text and preserved the exact IDs, passages, URLs, event
  times, observation times, source types, and relevance used in each assessment.
- Kept prompt construction, caching, validation, and qualification outside the
  provider adapter. The analysis service pins the structured-LLM capability for
  each run and rejects unknown citations, uncited publishable output, low-score
  `qualify` decisions, and qualification based only on CivicTracker or S&P
  membership evidence.
- Reused cached output only when the evidence hash, prompt/schema versions,
  provider, adapter version, and provider configuration are unchanged.
- Extended the canonical analysis contract with policy relevance and an
  earnings assessment, matching the documented version-one schema.
- Added bounded background analysis for `CVX`, read-only latest/history/evidence
  and outcome APIs, and a dashboard card showing decision, scores, thesis, bear
  case, catalysts, risks, uncertainty, provider, timestamp, and evidence link.
- Used the recorded structured-analysis fixture for the first safe end-to-end
  path. No external source data was sent to an AI service and no API key was
  requested or stored. A live hosted provider and authenticated encrypted
  credential unlock flow remain pending an explicit provider choice.
- Verified 111 offline tests, migration 5 on the existing database, a live local
  CVX `investigate` assessment, and HTTP 200 responses for the dashboard,
  assessment API, and cited-evidence API.

## 2026-08-20 — Encrypted provider-credential vault

- Added pinned `cryptography==48.0.0` and implemented the documented separation
  between password-based key derivation and authenticated secret encryption.
- Derived a 256-bit wrapping key with Argon2id using a random 16-byte salt and
  stored each credential in its own AES-256-GCM envelope with a fresh 12-byte
  nonce. Authenticated associated data binds each ciphertext to its opaque
  reference and credential kind.
- Stored only versioned salts, work factors, nonces, ciphertexts, and tags in
  owner-only files below `data/credentials`; no plaintext secret, unlock value,
  or usable key enters configuration, DuckDB, logs, URLs, or command arguments.
- Added hidden interactive-terminal commands for vault initialization, setting
  or replacing one secret, safe status, and explicit server unlock. The normal
  server command remains backward compatible and starts with credentials locked.
- Injected the credential store into provider management and added sanitized
  dashboard/API status for initialization, lock state, and reference count.
- Rejected wrong unlock values, modified envelopes, unsafe file permissions,
  invalid references, and secret access while locked with sanitized errors.
- Verified 114 offline tests plus live HTTP 200 checks for the dashboard, CSS,
  and `/api/v1/credentials/status`; no real credential was requested or created.
- Kept the hosted-LLM provider decision open. The vault is vendor-independent,
  so this security work does not commit the user to a paid API or retention policy.

## Current assumptions to validate experimentally

- The first baseline entry/exit algorithm should be simple, explainable, and parameterized; trend-pullback and breakout variants are leading candidates.
- Final output should use quality thresholds and a display cap rather than force exactly 10% of stocks to qualify.
- Intraday confirmation will likely use completed 15-minute bars while longer daily bars establish the primary trend.
- Precise strategy parameters must be chosen through walk-forward research and stability testing, not preference alone.

## Open decisions

No decision blocks building the ingestion, storage, AI-analysis, strategy-plugin, and backtest frameworks. Before calling entry/exit output production-ready, the baseline strategy family and its parameter acceptance criteria must be validated through research.

## Continuation Prompt

Please create a comprehensive project summary that would allow another AI assistant with no prior context to continue this development work seamlessly. Include:

- The project's purpose and core functionality
- Key technologies, frameworks, and libraries we've used
- The current architecture and component structure
- Implementation details of major features completed so far
- Known issues, limitations, or technical debt
- Immediate next steps and future development plans
- Any critical design decisions or tradeoffs made
