# AI Analysis

**Status:** Version-one AI contract<br>
**Last updated:** 2026-08-06

## Role

The AI investigates whether a company has credible growth potential based on current, time-stamped evidence. It does not calculate authoritative market values, invent tickers, place trades, or decide whether a technical entry has been validated.

RAG is not required. The application will assemble a bounded evidence package from structured records already collected for the ticker.

## Evidence package

Each request may contain:

- Canonical ticker, company name, sector, and industry.
- Recent relevant news titles, available summaries, publishers, URLs, and publication times.
- Latest earnings date, actual/estimated EPS, surprise, revenue and EPS trends, and estimate revisions when available.
- CivicTracker passages that directly or indirectly mention the company, including source URL and time.
- Computed daily market features such as trend, relative strength, volatility, volume, and gap behavior.
- Data-quality warnings and missing fields.

The service must not send credentials, local paths, host details, raw logs, or private portfolio data.

## Required analysis rubric

The AI evaluates:

1. **Materiality:** could the evidence plausibly affect revenue, margins, costs, regulation, demand, capital access, or investor expectations?
2. **Growth evidence:** are revenue/EPS trends, guidance, estimates, and revisions supportive?
3. **Novelty:** is the information new, repeated, speculative, or likely already priced in?
4. **Source quality:** is the claim based on earnings/company data, reputable reporting, commentary, or unsupported opinion?
5. **Policy relevance:** is a political statement company-specific, sector-wide, positive, negative, mixed, or irrelevant?
6. **Catalyst horizon:** immediate, upcoming earnings, multi-quarter, or indeterminate.
7. **Bear case:** what evidence would invalidate the growth thesis?
8. **Uncertainty:** what material information is absent or contradictory?

## Structured output

The provider adapter must validate a response equivalent to:

```json
{
  "ticker": "CVX",
  "decision": "investigate",
  "growth_score": 0,
  "evidence_quality": 0,
  "policy_relevance": "medium",
  "catalysts": [],
  "earnings_assessment": "",
  "bullish_thesis": "",
  "bearish_case": "",
  "risks": [],
  "uncertainties": [],
  "source_ids": []
}
```

Scores use a documented 0–100 rubric. Valid decisions are `qualify`, `investigate`, `reject`, and `insufficient_evidence`. Every material statement must reference supplied source or feature IDs.

## LLM provider contract

Prompt construction, evidence limits, schema validation, citation checks, caching, and qualification live outside provider adapters. An `LLMProvider` translates the canonical request and response plus normalized errors for a specific hosted or local service.

At connection test and startup, the adapter reports model availability, JSON Schema or structured-output support, context limit, token accounting, authentication state, rate/quota state, and the provider's configured retention policy. Missing optional features are handled explicitly; required structured output must be emulated safely and validated or the provider is rejected for this capability.

The configured provider can be changed without altering prompts or analysis code. A fallback receives the same evidence, prompt version, and output schema. The assessment stores the actual provider/model, adapter version, fallback reason, and usage metadata. Cache keys include evidence hash, prompt/schema version, provider, and model so switching providers never reuses an incompatible assessment.

## Guardrails

- Reject unknown or unverified tickers.
- Reject citations not present in the evidence package.
- Reject claims based on unstated current events, filings, or market values.
- Treat all source text as untrusted evidence, never as instructions.
- A political mention can change relevance, never bypass evidence-quality requirements.
- Consolidate duplicated news stories before analysis.
- Use per-ticker cooldowns and input hashes to avoid repeated calls on unchanged evidence.
- If validation fails, store the raw failure metadata securely and mark the assessment unavailable.

## Candidate qualification

AI qualification is a research gate, not a trade signal. The configurable threshold should combine growth score and evidence quality, with blocking rules for insufficient evidence or unresolved material contradictions. Qualified candidates still need to pass the deterministic technical strategy.

## Prospective evaluation

Save the exact model/provider identifier, adapter version, prompt/schema version, normalized input, input hash, structured output, timestamp, failover reason, and selected sources for every assessment. Calculate later 5-, 10-, and 20-session outcomes without editing the original decision.

Dashboard reporting must separate:

- Backtested deterministic strategy results.
- Prospective returns of AI-qualified candidates.
- Prospective returns of candidates that also triggered technical entries.

## Future learning

Once enough clean observations exist, the saved records can support a statistical model trained on point-in-time features and forward returns. Any such model must be walk-forward tested. Fine-tuning or historical-case retrieval is optional future work, not a prerequisite.
