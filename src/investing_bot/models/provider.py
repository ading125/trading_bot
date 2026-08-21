"""Canonical provider-neutral requests, records, and result provenance."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Generic, Self, TypeVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


Ticker = Annotated[
    str,
    StringConstraints(min_length=1, max_length=12, pattern=r"^[A-Z][A-Z0-9.-]*$"),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SchemaVersion = Annotated[
    str,
    StringConstraints(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*\.v[0-9]+$"),
]


class ProviderCapability(StrEnum):
    DAILY_BARS = "daily_bars"
    INTRADAY_BARS = "intraday_bars"
    QUOTES = "quotes"
    CORPORATE_ACTIONS = "corporate_actions"
    SYMBOL_LOOKUP = "symbol_lookup"
    NEWS = "news"
    EARNINGS = "earnings"
    SOCIAL_POSTS = "social_posts"
    STRUCTURED_LLM = "structured_llm"


class BarInterval(StrEnum):
    DAY_1 = "1d"
    MINUTE_15 = "15m"


class PriceAdjustment(StrEnum):
    RAW = "raw"
    ADJUSTED = "adjusted"


class CanonicalMetadata(BaseModel):
    """Lineage required on every canonical record returned by an adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    provider_record_id: str | None = Field(default=None, max_length=255)
    event_at: AwareDatetime
    known_available_at: AwareDatetime
    retrieved_at: AwareDatetime
    raw_payload_hash: Sha256Hex
    schema_version: SchemaVersion
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    dataset_lineage: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_point_in_time_order(self) -> Self:
        if self.known_available_at < self.event_at:
            raise ValueError("known_available_at must not precede event_at")
        if self.retrieved_at < self.known_available_at:
            raise ValueError("retrieved_at must not precede known_available_at")
        return self


class BarsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbols: tuple[Ticker, ...] = Field(min_length=1, max_length=500)
    start: AwareDatetime
    end: AwareDatetime
    interval: BarInterval
    adjustment: PriceAdjustment

    @model_validator(mode="after")
    def validate_range_and_symbols(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be later than start")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must not contain duplicates")
        if self.interval is BarInterval.DAY_1 and len(self.symbols) > 500:
            raise ValueError("daily requests are limited to 500 symbols")
        return self


class MarketBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Ticker
    interval: BarInterval
    bar_start: AwareDatetime
    bar_end: AwareDatetime
    session_date: date
    exchange_timezone: str = Field(min_length=1, max_length=64)
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: int = Field(ge=0)
    adjustment: PriceAdjustment
    metadata: CanonicalMetadata

    @model_validator(mode="after")
    def validate_bar(self) -> Self:
        if self.bar_end <= self.bar_start:
            raise ValueError("bar_end must be later than bar_start")
        if self.low > min(self.open, self.close):
            raise ValueError("low cannot exceed open or close")
        if self.high < max(self.open, self.close):
            raise ValueError("high cannot be below open or close")
        if self.high < self.low:
            raise ValueError("high cannot be below low")
        if self.metadata.event_at != self.bar_end:
            raise ValueError("market-bar event time must equal bar_end")
        return self


class QuotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbols: tuple[Ticker, ...] = Field(min_length=1, max_length=100)


class MarketQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: Ticker
    as_of: AwareDatetime
    price: float = Field(gt=0, allow_inf_nan=False)
    bid: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    ask: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)
    metadata: CanonicalMetadata

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")
        if self.as_of != self.metadata.event_at:
            raise ValueError("quote time must equal metadata event time")
        return self


class CorporateActionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbols: tuple[Ticker, ...] = Field(min_length=1, max_length=100)
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must be later than start")
        return self


class CorporateActionType(StrEnum):
    DIVIDEND = "dividend"
    SPLIT = "split"


class CorporateAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: Ticker
    action_type: CorporateActionType
    effective_at: AwareDatetime
    cash_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    split_ratio: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    metadata: CanonicalMetadata

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.action_type is CorporateActionType.DIVIDEND:
            if self.cash_amount is None or self.split_ratio is not None:
                raise ValueError("dividends require only cash_amount")
        if self.action_type is CorporateActionType.SPLIT:
            if self.split_ratio is None or self.cash_amount is not None:
                raise ValueError("splits require only split_ratio")
        if self.effective_at != self.metadata.event_at:
            raise ValueError("action time must equal metadata event time")
        return self


class SymbolLookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: str = Field(min_length=1, max_length=200)
    us_listed_only: bool = True


class SymbolMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: Ticker
    company_name: str = Field(min_length=1, max_length=300)
    exchange: str = Field(min_length=1, max_length=40)
    quote_type: str = Field(min_length=1, max_length=40)
    active: bool
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    metadata: CanonicalMetadata


class NewsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbols: tuple[Ticker, ...] = Field(min_length=1, max_length=100)
    published_after: AwareDatetime | None = None
    limit_per_symbol: int = Field(default=20, ge=1, le=100)


class NewsItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: Ticker
    headline: str = Field(min_length=1, max_length=1_000)
    summary: str = Field(default="", max_length=20_000)
    publisher: str = Field(min_length=1, max_length=300)
    url: str = Field(pattern=r"^https://", max_length=2_000)
    published_at: AwareDatetime
    metadata: CanonicalMetadata

    @model_validator(mode="after")
    def publication_matches_event_time(self) -> Self:
        if self.published_at != self.metadata.event_at:
            raise ValueError("news publication time must equal metadata event time")
        return self


class EarningsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbols: tuple[Ticker, ...] = Field(min_length=1, max_length=100)
    known_after: AwareDatetime | None = None


class EarningsEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: Ticker
    fiscal_period: str = Field(min_length=1, max_length=40)
    report_date: date
    reported_eps: float | None = Field(default=None, allow_inf_nan=False)
    estimated_eps: float | None = Field(default=None, allow_inf_nan=False)
    revenue: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    metadata: CanonicalMetadata


class SocialPostsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    member_id: str = Field(min_length=1, max_length=255)
    cursor: str | None = Field(default=None, max_length=255)
    page_size: int = Field(default=20, ge=1, le=100)


class SocialPostRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    post_id: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=100_000)
    original_url: str = Field(pattern=r"^https://", max_length=2_000)
    published_at: AwareDatetime
    is_media_only: bool
    is_deleted: bool = False
    content_hash: Sha256Hex
    metadata: CanonicalMetadata

    @model_validator(mode="after")
    def validate_content_state(self) -> Self:
        if self.is_media_only and self.content:
            raise ValueError("media-only posts cannot contain usable content")
        if not self.is_media_only and not self.content and not self.is_deleted:
            raise ValueError("empty active posts must be marked media-only")
        if self.published_at != self.metadata.event_at:
            raise ValueError("post publication time must equal metadata event time")
        return self


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=20_000)
    observed_at: AwareDatetime


class StructuredAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ticker: Ticker
    prompt_version: str = Field(pattern=r"^[a-z0-9_.-]+\.v[0-9]+$")
    output_schema_version: SchemaVersion
    evidence: tuple[EvidenceItem, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_source_ids(self) -> Self:
        ids = [item.source_id for item in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("evidence source IDs must be unique")
        return self


class AnalysisDecision(StrEnum):
    QUALIFY = "qualify"
    INVESTIGATE = "investigate"
    REJECT = "reject"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class PolicyRelevance(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StructuredAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ticker: Ticker
    decision: AnalysisDecision
    growth_score: int = Field(ge=0, le=100)
    evidence_quality: int = Field(ge=0, le=100)
    policy_relevance: PolicyRelevance
    catalysts: tuple[str, ...]
    earnings_assessment: str
    bullish_thesis: str
    bearish_case: str
    risks: tuple[str, ...]
    uncertainties: tuple[str, ...]
    source_ids: tuple[str, ...]
    metadata: CanonicalMetadata


class PageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    next_cursor: str | None = None
    has_more: bool

    @model_validator(mode="after")
    def validate_cursor(self) -> Self:
        if self.has_more and self.next_cursor is None:
            raise ValueError("has_more requires next_cursor")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("final page cannot include next_cursor")
        return self


class QuotaStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: int | None = Field(default=None, ge=0)
    remaining: int | None = Field(default=None, ge=0)
    resets_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def remaining_not_above_limit(self) -> Self:
        if (
            self.limit is not None
            and self.remaining is not None
            and self.remaining > self.limit
        ):
            raise ValueError("remaining quota cannot exceed limit")
        return self


class TokenUsage(BaseModel):
    """Provider-neutral token accounting for one model request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def total_covers_input_and_output(self) -> Self:
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("total tokens cannot be below input plus output tokens")
        return self


class FallbackAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str
    error_code: str
    occurred_at: AwareDatetime


class ProviderProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str | None = None
    capability: ProviderCapability
    provider_id: str
    adapter_version: str
    schema_version: SchemaVersion
    dataset_lineage: str
    request_id: str
    configuration_hash: Sha256Hex | None = None
    fallback_attempts: tuple[FallbackAttempt, ...] = ()


T = TypeVar("T")


class ProviderResult(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[T, ...]
    provenance: ProviderProvenance
    page: PageInfo | None = None
    quota: QuotaStatus | None = None
    usage: TokenUsage | None = None
