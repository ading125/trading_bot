# Security

**Status:** Required controls<br>
**Last updated:** 2026-08-03

## Threat model

Protect against leaked LLM credentials, exposed local ports, malicious source text, unsafe strategy code, dependency compromise, tampered market files, sensitive logs, and unencrypted backups.

## Container and network controls

- Publish the dashboard only through an explicit `127.0.0.1` mapping.
- Require an authenticated local session despite loopback binding.
- Enforce CSRF tokens and same-origin checks for mutations.
- Run the container as non-root with dropped capabilities and `no-new-privileges`.
- Keep application code read-only and use `tmpfs` for temporary writes.
- Permit outbound access only where required by configured source and AI providers when practical.
- Do not include brokerage permissions or order endpoints in version one.

## Secrets

`yfinance` and the public CivicTracker feed do not require private API keys. Hosted-LLM credentials do.

- Encrypt stored credentials with an authenticated encryption scheme.
- Derive the wrapping key from a local unlock secret using Argon2id.
- Keep decrypted keys in memory only while unlocked.
- Never store usable secrets in source, Git, Docker image layers, Compose files, environment variables, URLs, command arguments, DuckDB plaintext fields, logs, reports, or diagnostics.
- An optional Windows launcher may retrieve the unlock secret from Windows Credential Manager and pass it to a one-shot container command via standard input.
- Settings APIs return only configured/not-configured status.

Password hashing, secret encryption, and content hashing are different controls: Argon2id verifies/derives from an unlock secret; authenticated encryption protects retrievable provider credentials; SHA-256 identifies source/data content and detects changes.

## Untrusted content and AI

- Source text is data, not instructions.
- Strip active markup and never execute source HTML or JavaScript.
- Do not let AI output create arbitrary URLs, tickers, queries, shell commands, or strategy code.
- Validate all structured AI output against strict schemas and known identifiers.
- Keep source excerpts and model responses escaped in dashboard rendering.

## Strategy-code policy

Python strategies are trusted executable code. They must be reviewed, committed, tested, registered explicitly, and packaged into the image. Do not load strategy code uploaded through the dashboard or stored in the writable data volume.

## Integrity, logs, and backups

- Hash raw source responses, canonical datasets, configurations, and published reports.
- Redact credentials, authorization headers, unlock material, and sensitive exception context.
- Rotate local logs and bound retention.
- Produce sanitized diagnostic bundles through an explicit action.
- Encrypt backups containing credentials or private configuration.
- Verify restore into a new named volume before relying on a backup procedure.
- Pin dependencies and image digests; upgrades are deliberate and tested, never automatic.

## Security acceptance checks

- LAN hosts cannot reach the dashboard using default configuration.
- No plaintext secret appears in the repository, image history, `docker inspect`, volume scan, logs, or exports.
- Malicious instructions embedded in a CivicTracker post or news item cannot alter application behavior.
- Tampered or schema-invalid data is quarantined rather than used for signals.
- A locked or unavailable LLM never prevents deterministic data/strategy operation.
