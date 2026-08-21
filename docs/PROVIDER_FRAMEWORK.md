# Provider Framework

**Status:** Provider framework plus CivicTracker and Yahoo adapters implemented<br>
**Last updated:** 2026-08-20

## Purpose

External APIs are selected by capability so market data, news, earnings,
social posts, symbol lookup, and AI services can change without changing
business logic. Only providers registered in application source may run; a
configuration file cannot load arbitrary Python or supply an upstream URL.

## Capabilities

The current contracts cover daily bars, 15-minute bars, quotes, corporate
actions, symbol lookup, news, earnings, cursor-based social posts, and
structured AI analysis. Each capability has a narrow protocol and canonical
request, record, result, error, health, quota, and provenance models.

Every canonical record preserves the actual provider, adapter version, schema
version, raw-payload hash, event time, known-available time, retrieval time,
and dataset lineage. A result adds its request ID, run ID, configuration hash,
and recorded fallback attempts.

## Selection and pinning

The optional local file `/data/providers.json` selects a primary provider and
ordered fallbacks for each capability. [providers.example.json](../providers.example.json)
shows the complete schema. If that file is absent, deterministic recorded
fixtures are selected so the framework can operate without external accounts.

At the start of a unit of work, the provider manager:

1. Validates every configured provider and capability against the packaged registry.
2. Checks that authenticated providers reference a configured opaque credential ID.
3. Runs a sanitized connection test in configured order.
4. Records unavailable attempts and pins the first available provider to the run.
5. Rejects a later provider switch after work has begun.

Fallback is allowed for explicit transport, authentication, quota, rate-limit,
schema, staleness, temporary-availability, and unsupported-capability failures.
Invalid requests and ordinary not-found results do not trigger fallback.

## Credentials

Provider configuration contains only references shaped like
`cred_provider_name`; it never contains a usable secret. `EncryptedCredentialStore`
derives a 256-bit wrapping key from a terminal-entered unlock secret with
Argon2id and stores each provider secret in a separate AES-256-GCM envelope.
The reference and credential kind are authenticated as associated data. Vault
and envelope files use owner-only permissions, decrypted values exist only while
the process is explicitly unlocked, and the dashboard/API expose only sanitized
presence and lock status. A provider adapter may resolve only its configured
opaque reference through the injected store.

## Health and discovery

Read-only local endpoints expose sanitized framework state:

- `GET /api/v1/providers` — manifests, supported capabilities, and active selections.
- `GET /api/v1/providers/health` — capability health, latency, sanitized error class, and quota state.

An unhealthy optional provider does not make the database or deterministic
application functions unready. Its capability is reported independently.

## Recorded fixtures

Two interchangeable packaged providers use the same immutable recorded
research slice. They exercise SPY/CVX bars, quotes, a corporate action,
Chevron symbol lookup, news, earnings, CivicTracker-style cursor pages, and a
source-bounded structured CVX analysis. These providers make contract tests
deterministic; they are not live market-data sources.

The live CivicTracker JSON adapter and its HTML fallback are implemented in
Milestone 3. The Yahoo market/event/symbol adapter is implemented in Milestone 4
and is used through the provider-neutral resolver in Milestone 5. The recorded
structured-LLM provider now exercises the complete Milestone 6 evidence,
validation, caching, persistence, API, and dashboard path. A live hosted-LLM
adapter remains pending a provider choice; the encrypted vault and unlock flow
are implemented independently of that choice.
