"""Configuration validation, health checks, pinning, and controlled fallback."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from investing_bot.models import (
    BarInterval,
    BarsRequest,
    CorporateAction,
    CorporateActionsRequest,
    EarningsEvent,
    EarningsRequest,
    FallbackAttempt,
    MarketBar,
    MarketQuote,
    NewsItem,
    NewsRequest,
    ProviderCapability,
    ProviderResult,
    QuotesRequest,
    SocialPostRecord,
    SocialPostsRequest,
    StructuredAnalysis,
    StructuredAnalysisRequest,
    SymbolLookupRequest,
    SymbolMatch,
)
from investing_bot.providers.contracts import (
    ConnectionTestResult,
    ProviderCallError,
    ProviderConfiguration,
    ProviderContractError,
    ProviderErrorCode,
    ProviderFailure,
    ProviderHealthState,
    ProviderManifest,
    ProviderTarget,
)
from investing_bot.providers.credentials import CredentialReferenceStore
from investing_bot.providers.protocols import (
    BaseProvider,
    CorporateActionsProvider,
    EarningsProvider,
    MarketDataProvider,
    NewsProvider,
    QuotesProvider,
    SocialPostsProvider,
    StructuredLLMProvider,
    SymbolLookupProvider,
)
from investing_bot.providers.registry import ProviderRegistry, ProviderRegistryError


class ProviderConfigurationError(ValueError):
    pass


_FALLBACK_CODES = frozenset(
    {
        ProviderErrorCode.AUTHENTICATION,
        ProviderErrorCode.QUOTA_EXHAUSTED,
        ProviderErrorCode.RATE_LIMITED,
        ProviderErrorCode.SCHEMA_INCOMPATIBLE,
        ProviderErrorCode.STALE,
        ProviderErrorCode.TEMPORARILY_UNAVAILABLE,
        ProviderErrorCode.TRANSPORT,
        ProviderErrorCode.UNSUPPORTED,
    }
)


class ProviderManager:
    """Resolve configured providers and pin exactly one adapter per run."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        configuration: ProviderConfiguration,
        credentials: CredentialReferenceStore,
    ) -> None:
        self.registry = registry
        self.configuration = configuration
        self.credentials = credentials
        self._health: dict[tuple[str, ProviderCapability], ConnectionTestResult] = {}
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        for capability, selection in self.configuration.selections.items():
            for target in selection.ordered_targets:
                try:
                    manifest = self.registry.manifest(target.provider_id)
                except ProviderRegistryError as exc:
                    raise ProviderConfigurationError(str(exc)) from exc
                if capability not in manifest.capabilities:
                    raise ProviderConfigurationError(
                        f"provider {target.provider_id} does not support {capability.value}"
                    )
                if manifest.authentication_required and target.credential_ref is None:
                    raise ProviderConfigurationError(
                        f"provider {target.provider_id} requires a credential reference"
                    )
                if not manifest.authentication_required and target.credential_ref is not None:
                    raise ProviderConfigurationError(
                        f"provider {target.provider_id} does not accept credentials"
                    )

    async def refresh_health(self) -> tuple[ConnectionTestResult, ...]:
        """Test each configured provider/capability without exposing secrets."""

        results: list[ConnectionTestResult] = []
        seen: set[tuple[str, ProviderCapability]] = set()
        for capability, selection in self.configuration.selections.items():
            for target in selection.ordered_targets:
                key = (target.provider_id, capability)
                if key in seen:
                    continue
                seen.add(key)
                result = await self._test_target(capability, target)
                self._health[key] = result
                results.append(result)
        return tuple(sorted(results, key=lambda item: (item.capability, item.provider_id)))

    def health_snapshot(self) -> tuple[ConnectionTestResult, ...]:
        return tuple(
            sorted(
                self._health.values(),
                key=lambda item: (item.capability, item.provider_id),
            )
        )

    async def pin(self, capability: ProviderCapability, *, run_id: str) -> PinnedProvider:
        """Select once before work begins and preserve every failed attempt."""

        selection = self.configuration.selections.get(capability)
        if selection is None:
            raise ProviderCallError(
                ProviderFailure(
                    provider_id="provider_manager",
                    capability=capability,
                    code=ProviderErrorCode.UNSUPPORTED,
                    safe_message=f"no provider configured for {capability.value}",
                    retryable=False,
                )
            )

        attempts: list[FallbackAttempt] = []
        last_result: ConnectionTestResult | None = None
        for target in selection.ordered_targets:
            result = await self._test_target(capability, target)
            self._health[(target.provider_id, capability)] = result
            last_result = result
            if result.available:
                return PinnedProvider(
                    provider=self.registry.create(target.provider_id),
                    manifest=self.registry.manifest(target.provider_id),
                    capability=capability,
                    run_id=run_id,
                    configuration_hash=self.configuration.configuration_hash,
                    fallback_attempts=tuple(attempts),
                )
            error_code = result.error_code or ProviderErrorCode.TEMPORARILY_UNAVAILABLE
            attempts.append(
                FallbackAttempt(
                    provider_id=target.provider_id,
                    error_code=error_code.value,
                    occurred_at=result.checked_at,
                )
            )
            if error_code not in _FALLBACK_CODES:
                break

        provider_id = (
            last_result.provider_id if last_result is not None else selection.primary.provider_id
        )
        code = (
            last_result.error_code
            if last_result is not None and last_result.error_code is not None
            else ProviderErrorCode.TEMPORARILY_UNAVAILABLE
        )
        raise ProviderCallError(
            ProviderFailure(
                provider_id=provider_id,
                capability=capability,
                code=code,
                safe_message=f"no available provider for {capability.value}",
                retryable=code in _FALLBACK_CODES,
            )
        )

    async def _test_target(
        self, capability: ProviderCapability, target: ProviderTarget
    ) -> ConnectionTestResult:
        manifest = self.registry.manifest(target.provider_id)
        if (
            manifest.authentication_required
            and target.credential_ref is not None
            and not self.credentials.is_configured(target.credential_ref)
        ):
            return ConnectionTestResult(
                provider_id=target.provider_id,
                capability=capability,
                state=ProviderHealthState.LOCKED,
                checked_at=datetime.now(UTC),
                latency_ms=0,
                error_code=ProviderErrorCode.AUTHENTICATION,
                message="configured credential is locked or unavailable",
            )
        provider = self.registry.create(target.provider_id)
        try:
            result = await provider.test_connection(capability, target.credential_ref)
        except ProviderCallError as exc:
            return ConnectionTestResult(
                provider_id=target.provider_id,
                capability=capability,
                state=ProviderHealthState.UNHEALTHY,
                checked_at=datetime.now(UTC),
                latency_ms=0,
                error_code=exc.failure.code,
                message=exc.failure.safe_message,
            )
        if result.provider_id != target.provider_id or result.capability is not capability:
            raise ProviderContractError("connection test identity does not match target")
        return result


R = TypeVar("R")


class PinnedProvider:
    """One immutable adapter choice for one capability and run."""

    def __init__(
        self,
        *,
        provider: BaseProvider,
        manifest: ProviderManifest,
        capability: ProviderCapability,
        run_id: str,
        configuration_hash: str,
        fallback_attempts: tuple[FallbackAttempt, ...],
    ) -> None:
        self._provider = provider
        self.manifest = manifest
        self.capability = capability
        self.run_id = run_id
        self.configuration_hash = configuration_hash
        self.fallback_attempts = fallback_attempts

    @property
    def provider_id(self) -> str:
        return self.manifest.provider_id

    async def fetch_bars(self, request: BarsRequest) -> ProviderResult[MarketBar]:
        expected = (
            ProviderCapability.DAILY_BARS
            if request.interval is BarInterval.DAY_1
            else ProviderCapability.INTRADAY_BARS
        )
        self._require_capability(expected)
        self._require_protocol(MarketDataProvider)
        provider = cast(MarketDataProvider, self._provider)
        return self._stamp(await provider.fetch_bars(request))

    async def fetch_quotes(self, request: QuotesRequest) -> ProviderResult[MarketQuote]:
        self._require_capability(ProviderCapability.QUOTES)
        self._require_protocol(QuotesProvider)
        provider = cast(QuotesProvider, self._provider)
        return self._stamp(await provider.fetch_quotes(request))

    async def fetch_corporate_actions(
        self, request: CorporateActionsRequest
    ) -> ProviderResult[CorporateAction]:
        self._require_capability(ProviderCapability.CORPORATE_ACTIONS)
        self._require_protocol(CorporateActionsProvider)
        provider = cast(CorporateActionsProvider, self._provider)
        return self._stamp(await provider.fetch_corporate_actions(request))

    async def lookup_symbols(
        self, request: SymbolLookupRequest
    ) -> ProviderResult[SymbolMatch]:
        self._require_capability(ProviderCapability.SYMBOL_LOOKUP)
        self._require_protocol(SymbolLookupProvider)
        provider = cast(SymbolLookupProvider, self._provider)
        return self._stamp(await provider.lookup_symbols(request))

    async def fetch_news(self, request: NewsRequest) -> ProviderResult[NewsItem]:
        self._require_capability(ProviderCapability.NEWS)
        self._require_protocol(NewsProvider)
        provider = cast(NewsProvider, self._provider)
        return self._stamp(await provider.fetch_news(request))

    async def fetch_earnings(
        self, request: EarningsRequest
    ) -> ProviderResult[EarningsEvent]:
        self._require_capability(ProviderCapability.EARNINGS)
        self._require_protocol(EarningsProvider)
        provider = cast(EarningsProvider, self._provider)
        return self._stamp(await provider.fetch_earnings(request))

    async def fetch_social_posts(
        self, request: SocialPostsRequest
    ) -> ProviderResult[SocialPostRecord]:
        self._require_capability(ProviderCapability.SOCIAL_POSTS)
        self._require_protocol(SocialPostsProvider)
        provider = cast(SocialPostsProvider, self._provider)
        return self._stamp(await provider.fetch_social_posts(request))

    async def analyze(
        self, request: StructuredAnalysisRequest
    ) -> ProviderResult[StructuredAnalysis]:
        self._require_capability(ProviderCapability.STRUCTURED_LLM)
        self._require_protocol(StructuredLLMProvider)
        expected_schema = self.manifest.schema_versions[self.capability]
        if request.output_schema_version != expected_schema:
            raise ProviderContractError(
                "analysis output schema does not match the pinned provider contract"
            )
        provider = cast(StructuredLLMProvider, self._provider)
        result = await provider.analyze(request)
        allowed_ids = {item.source_id for item in request.evidence}
        for analysis in result.items:
            if analysis.ticker != request.ticker:
                raise ProviderContractError("analysis ticker does not match request")
            if not set(analysis.source_ids).issubset(allowed_ids):
                raise ProviderContractError("analysis cited unknown evidence")
        return self._stamp(result)

    def _require_capability(self, expected: ProviderCapability) -> None:
        if self.capability is not expected:
            raise ProviderContractError(
                f"provider is pinned for {self.capability.value}, not {expected.value}"
            )

    def _require_protocol(self, protocol: type[Any]) -> None:
        if not isinstance(self._provider, protocol):
            raise ProviderContractError(
                f"provider {self.provider_id} does not implement its declared protocol"
            )

    def _stamp(self, result: ProviderResult[R]) -> ProviderResult[R]:
        provenance = result.provenance
        expected_schema = self.manifest.schema_versions[self.capability]
        if (
            provenance.provider_id != self.provider_id
            or provenance.adapter_version != self.manifest.adapter_version
            or provenance.capability is not self.capability
            or provenance.schema_version != expected_schema
        ):
            raise ProviderContractError("result provenance violates provider manifest")
        for item in result.items:
            metadata = getattr(item, "metadata", None)
            if metadata is None:
                raise ProviderContractError("canonical record is missing metadata")
            if (
                metadata.provider_id != self.provider_id
                or metadata.adapter_version != self.manifest.adapter_version
                or metadata.schema_version != expected_schema
                or metadata.dataset_lineage != provenance.dataset_lineage
            ):
                raise ProviderContractError("record lineage does not match result provenance")
        stamped = provenance.model_copy(
            update={
                "run_id": self.run_id,
                "configuration_hash": self.configuration_hash,
                "fallback_attempts": self.fallback_attempts,
            }
        )
        return result.model_copy(update={"provenance": stamped})
