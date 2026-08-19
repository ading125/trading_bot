"""Narrow runtime-checkable protocols for each external capability."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from investing_bot.models import (
    BarsRequest,
    CorporateAction,
    CorporateActionsRequest,
    EarningsEvent,
    EarningsRequest,
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
    CredentialReference,
    ProviderManifest,
)


@runtime_checkable
class BaseProvider(Protocol):
    @property
    def manifest(self) -> ProviderManifest: ...

    async def test_connection(
        self,
        capability: ProviderCapability,
        credential_ref: CredentialReference | None,
    ) -> ConnectionTestResult: ...


@runtime_checkable
class MarketDataProvider(BaseProvider, Protocol):
    async def fetch_bars(self, request: BarsRequest) -> ProviderResult[MarketBar]: ...


@runtime_checkable
class QuotesProvider(BaseProvider, Protocol):
    async def fetch_quotes(
        self, request: QuotesRequest
    ) -> ProviderResult[MarketQuote]: ...


@runtime_checkable
class CorporateActionsProvider(BaseProvider, Protocol):
    async def fetch_corporate_actions(
        self, request: CorporateActionsRequest
    ) -> ProviderResult[CorporateAction]: ...


@runtime_checkable
class SymbolLookupProvider(BaseProvider, Protocol):
    async def lookup_symbols(
        self, request: SymbolLookupRequest
    ) -> ProviderResult[SymbolMatch]: ...


@runtime_checkable
class NewsProvider(BaseProvider, Protocol):
    async def fetch_news(self, request: NewsRequest) -> ProviderResult[NewsItem]: ...


@runtime_checkable
class EarningsProvider(BaseProvider, Protocol):
    async def fetch_earnings(
        self, request: EarningsRequest
    ) -> ProviderResult[EarningsEvent]: ...


@runtime_checkable
class SocialPostsProvider(BaseProvider, Protocol):
    async def fetch_social_posts(
        self, request: SocialPostsRequest
    ) -> ProviderResult[SocialPostRecord]: ...


@runtime_checkable
class StructuredLLMProvider(BaseProvider, Protocol):
    async def analyze(
        self, request: StructuredAnalysisRequest
    ) -> ProviderResult[StructuredAnalysis]: ...
