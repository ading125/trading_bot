"""Recorded provider implementing every version-one capability contract."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from importlib.resources import files
import json
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from investing_bot.models import (
    BarInterval,
    BarsRequest,
    CanonicalMetadata,
    CorporateAction,
    CorporateActionsRequest,
    EarningsEvent,
    EarningsRequest,
    MarketBar,
    MarketQuote,
    NewsItem,
    NewsRequest,
    PageInfo,
    ProviderCapability,
    ProviderProvenance,
    ProviderResult,
    QuotaStatus,
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
    ProviderCallError,
    ProviderErrorCode,
    ProviderFailure,
    ProviderHealthState,
    ProviderManifest,
    RateLimitPolicy,
)


_DATA_PACKAGE = "investing_bot.providers.fixtures.data"
_SCHEMAS = {
    ProviderCapability.DAILY_BARS: "market_bar.v1",
    ProviderCapability.INTRADAY_BARS: "market_bar.v1",
    ProviderCapability.QUOTES: "market_quote.v1",
    ProviderCapability.CORPORATE_ACTIONS: "corporate_action.v1",
    ProviderCapability.SYMBOL_LOOKUP: "symbol_match.v1",
    ProviderCapability.NEWS: "news_item.v1",
    ProviderCapability.EARNINGS: "earnings_event.v1",
    ProviderCapability.SOCIAL_POSTS: "social_post.v1",
    ProviderCapability.STRUCTURED_LLM: "structured_analysis.v1",
}


def load_recorded_payload(name: str = "research_slice.json") -> dict[str, Any]:
    text = files(_DATA_PACKAGE).joinpath(name).read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("recorded provider payload must be an object")
    return payload


def build_fixture_manifest(
    provider_id: str,
    *,
    authentication_required: bool = False,
    capabilities: frozenset[ProviderCapability] | None = None,
) -> ProviderManifest:
    supported = capabilities or frozenset(_SCHEMAS)
    return ProviderManifest(
        provider_id=provider_id,
        display_name=f"Recorded Fixture ({provider_id})",
        adapter_version="1.0.0",
        capabilities=supported,
        schema_versions={capability: _SCHEMAS[capability] for capability in supported},
        authentication_required=authentication_required,
        credential_kind="api_token" if authentication_required else None,
        rate_limit=RateLimitPolicy(requests=1_000, window_seconds=3_600),
        supported_intervals=frozenset({"1d", "15m"}),
        optional_features=frozenset({"recorded_payloads", "cursor_pagination"}),
        allowed_origins=(),
        fixture=True,
    )


class RecordedFixtureProvider:
    """Deterministic adapter backed by immutable packaged JSON responses."""

    def __init__(
        self,
        provider_id: str,
        *,
        payload: dict[str, Any] | None = None,
        health_overrides: dict[
            ProviderCapability, tuple[ProviderHealthState, ProviderErrorCode | None]
        ]
        | None = None,
        failure_overrides: dict[ProviderCapability, ProviderErrorCode] | None = None,
        authentication_required: bool = False,
        capabilities: frozenset[ProviderCapability] | None = None,
    ) -> None:
        self._manifest = build_fixture_manifest(
            provider_id,
            authentication_required=authentication_required,
            capabilities=capabilities,
        )
        self._payload = deepcopy(payload or load_recorded_payload())
        self._health_overrides = health_overrides or {}
        self._failure_overrides = failure_overrides or {}
        self._retrieved_at = TypeAdapter(datetime).validate_python(
            self._payload["retrieved_at"]
        ).astimezone(UTC)

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    async def test_connection(
        self,
        capability: ProviderCapability,
        credential_ref: CredentialReference | None,
    ) -> ConnectionTestResult:
        if capability not in self.manifest.capabilities:
            return ConnectionTestResult(
                provider_id=self.manifest.provider_id,
                capability=capability,
                state=ProviderHealthState.UNSUPPORTED,
                checked_at=datetime.now(UTC),
                latency_ms=1,
                error_code=ProviderErrorCode.UNSUPPORTED,
                message="capability is not supported",
            )
        state, error = self._health_overrides.get(
            capability, (ProviderHealthState.HEALTHY, None)
        )
        return ConnectionTestResult(
            provider_id=self.manifest.provider_id,
            capability=capability,
            state=state,
            checked_at=datetime.now(UTC),
            latency_ms=1,
            error_code=error,
            message=None if state is ProviderHealthState.HEALTHY else "fixture health override",
            quota=QuotaStatus(limit=1_000, remaining=999),
        )

    async def fetch_bars(self, request: BarsRequest) -> ProviderResult[MarketBar]:
        capability = (
            ProviderCapability.DAILY_BARS
            if request.interval is BarInterval.DAY_1
            else ProviderCapability.INTRADAY_BARS
        )
        self._maybe_fail(capability)
        records: list[MarketBar] = []
        for raw in self._payload["bars"]:
            if (
                raw["symbol"] not in request.symbols
                or raw["interval"] != request.interval.value
                or raw["adjustment"] != request.adjustment.value
            ):
                continue
            bar_end = TypeAdapter(datetime).validate_python(raw["bar_end"])
            if not request.start <= bar_end <= request.end:
                continue
            records.append(
                self._validate(
                    MarketBar,
                    {
                        **_without(raw, "id"),
                        "metadata": self._metadata(
                            raw,
                            event_at=bar_end,
                            capability=capability,
                        ),
                    },
                    capability,
                )
            )
        return self._result(capability, records)

    async def fetch_quotes(
        self, request: QuotesRequest
    ) -> ProviderResult[MarketQuote]:
        capability = ProviderCapability.QUOTES
        self._maybe_fail(capability)
        records = [
            self._validate(
                MarketQuote,
                {
                    **_without(raw, "id"),
                    "metadata": self._metadata(
                        raw,
                        event_at=TypeAdapter(datetime).validate_python(raw["as_of"]),
                        capability=capability,
                    ),
                },
                capability,
            )
            for raw in self._payload["quotes"]
            if raw["symbol"] in request.symbols
        ]
        return self._result(capability, records)

    async def fetch_corporate_actions(
        self, request: CorporateActionsRequest
    ) -> ProviderResult[CorporateAction]:
        capability = ProviderCapability.CORPORATE_ACTIONS
        self._maybe_fail(capability)
        records: list[CorporateAction] = []
        for raw in self._payload["corporate_actions"]:
            event_at = TypeAdapter(datetime).validate_python(raw["effective_at"])
            if raw["symbol"] not in request.symbols or not (
                request.start <= event_at <= request.end
            ):
                continue
            records.append(
                self._validate(
                    CorporateAction,
                    {
                        **_without(raw, "id"),
                        "metadata": self._metadata(
                            raw, event_at=event_at, capability=capability
                        ),
                    },
                    capability,
                )
            )
        return self._result(capability, records)

    async def lookup_symbols(
        self, request: SymbolLookupRequest
    ) -> ProviderResult[SymbolMatch]:
        capability = ProviderCapability.SYMBOL_LOOKUP
        self._maybe_fail(capability)
        query = request.query.casefold()
        records: list[SymbolMatch] = []
        for raw in self._payload["symbols"]:
            if query not in raw["symbol"].casefold() and query not in raw[
                "company_name"
            ].casefold():
                continue
            event_at = TypeAdapter(datetime).validate_python(raw["event_at"])
            records.append(
                self._validate(
                    SymbolMatch,
                    {
                        **_without(raw, "id", "event_at"),
                        "metadata": self._metadata(
                            raw, event_at=event_at, capability=capability
                        ),
                    },
                    capability,
                )
            )
        return self._result(capability, records)

    async def fetch_news(self, request: NewsRequest) -> ProviderResult[NewsItem]:
        capability = ProviderCapability.NEWS
        self._maybe_fail(capability)
        counts: dict[str, int] = {}
        records: list[NewsItem] = []
        for raw in self._payload["news"]:
            published_at = TypeAdapter(datetime).validate_python(raw["published_at"])
            if raw["symbol"] not in request.symbols:
                continue
            if request.published_after is not None and published_at <= request.published_after:
                continue
            if counts.get(raw["symbol"], 0) >= request.limit_per_symbol:
                continue
            records.append(
                self._validate(
                    NewsItem,
                    {
                        **_without(raw, "id"),
                        "metadata": self._metadata(
                            raw, event_at=published_at, capability=capability
                        ),
                    },
                    capability,
                )
            )
            counts[raw["symbol"]] = counts.get(raw["symbol"], 0) + 1
        return self._result(capability, records)

    async def fetch_earnings(
        self, request: EarningsRequest
    ) -> ProviderResult[EarningsEvent]:
        capability = ProviderCapability.EARNINGS
        self._maybe_fail(capability)
        records: list[EarningsEvent] = []
        for raw in self._payload["earnings"]:
            if raw["symbol"] not in request.symbols:
                continue
            event_at = TypeAdapter(datetime).validate_python(raw["event_at"])
            known_at = TypeAdapter(datetime).validate_python(raw["known_available_at"])
            if request.known_after is not None and known_at <= request.known_after:
                continue
            records.append(
                self._validate(
                    EarningsEvent,
                    {
                        **_without(raw, "id", "event_at", "known_available_at"),
                        "metadata": self._metadata(
                            raw,
                            event_at=event_at,
                            known_available_at=known_at,
                            capability=capability,
                        ),
                    },
                    capability,
                )
            )
        return self._result(capability, records)

    async def fetch_social_posts(
        self, request: SocialPostsRequest
    ) -> ProviderResult[SocialPostRecord]:
        capability = ProviderCapability.SOCIAL_POSTS
        self._maybe_fail(capability)
        try:
            offset = int(request.cursor or "0")
        except ValueError as exc:
            raise self._call_error(
                capability,
                ProviderErrorCode.INVALID_REQUEST,
                "social-post cursor is invalid",
                retryable=False,
            ) from exc
        if offset < 0:
            raise self._call_error(
                capability,
                ProviderErrorCode.INVALID_REQUEST,
                "social-post cursor is invalid",
                retryable=False,
            )
        matching = [
            raw
            for raw in self._payload["social_posts"]
            if raw["member_id"] == request.member_id
        ]
        page_raw = matching[offset : offset + request.page_size]
        records: list[SocialPostRecord] = []
        for raw in page_raw:
            published_at = TypeAdapter(datetime).validate_python(raw["published_at"])
            content_hash = sha256(
                json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            records.append(
                self._validate(
                    SocialPostRecord,
                    {
                        **_without(raw, "id", "member_id"),
                        "content_hash": content_hash,
                        "metadata": self._metadata(
                            raw, event_at=published_at, capability=capability
                        ),
                    },
                    capability,
                )
            )
        next_offset = offset + len(page_raw)
        has_more = next_offset < len(matching)
        return self._result(
            capability,
            records,
            page=PageInfo(
                next_cursor=str(next_offset) if has_more else None,
                has_more=has_more,
            ),
        )

    async def analyze(
        self, request: StructuredAnalysisRequest
    ) -> ProviderResult[StructuredAnalysis]:
        capability = ProviderCapability.STRUCTURED_LLM
        self._maybe_fail(capability)
        allowed_ids = {item.source_id for item in request.evidence}
        raw = next(
            (
                item
                for item in self._payload["analyses"]
                if item["ticker"] == request.ticker
            ),
            None,
        )
        if raw is None:
            raw = {
                "id": f"analysis-{request.ticker.casefold()}",
                "ticker": request.ticker,
                "decision": "insufficient_evidence",
                "growth_score": 0,
                "evidence_quality": 0,
                "policy_relevance": "none",
                "catalysts": [],
                "earnings_assessment": "",
                "bullish_thesis": "",
                "bearish_case": "",
                "risks": [],
                "uncertainties": ["No recorded analysis fixture"],
                "source_ids": [],
            }
        mapped_source_ids = [
            item for item in raw["source_ids"] if item in allowed_ids
        ]
        if raw["source_ids"] and not mapped_source_ids and request.evidence:
            # The packaged fixture names its recorded source, while application
            # evidence uses durable hashed IDs. Publish only an ID that was
            # actually supplied in this request.
            mapped_source_ids = [request.evidence[0].source_id]
        raw = {**raw, "source_ids": mapped_source_ids}
        event_at = max(item.observed_at for item in request.evidence)
        record = self._validate(
            StructuredAnalysis,
            {
                **_without(raw, "id"),
                "metadata": self._metadata(
                    raw,
                    event_at=event_at,
                    known_available_at=event_at,
                    capability=capability,
                ),
            },
            capability,
        )
        return self._result(capability, [record])

    def _metadata(
        self,
        raw: dict[str, Any],
        *,
        event_at: datetime,
        capability: ProviderCapability,
        known_available_at: datetime | None = None,
    ) -> CanonicalMetadata:
        payload_hash = sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            return CanonicalMetadata(
                provider_id=self.manifest.provider_id,
                provider_record_id=str(raw["id"]),
                event_at=event_at,
                known_available_at=known_available_at or event_at,
                retrieved_at=max(self._retrieved_at, known_available_at or event_at),
                raw_payload_hash=payload_hash,
                schema_version=self.manifest.schema_versions[capability],
                adapter_version=self.manifest.adapter_version,
                dataset_lineage=f"fixture:research_slice:v1:{self.manifest.provider_id}",
            )
        except ValidationError as exc:
            raise self._call_error(
                capability,
                ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                "recorded response metadata failed canonical validation",
                retryable=False,
            ) from exc

    def _validate(
        self,
        model: type[FixtureModel],
        payload: dict[str, Any],
        capability: ProviderCapability,
    ) -> FixtureModel:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise self._call_error(
                capability,
                ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                "recorded response failed canonical schema validation",
                retryable=False,
            ) from exc

    def _result(
        self,
        capability: ProviderCapability,
        records: list[Any],
        *,
        page: PageInfo | None = None,
    ) -> ProviderResult[Any]:
        return ProviderResult(
            items=tuple(records),
            provenance=ProviderProvenance(
                capability=capability,
                provider_id=self.manifest.provider_id,
                adapter_version=self.manifest.adapter_version,
                schema_version=self.manifest.schema_versions[capability],
                dataset_lineage=f"fixture:research_slice:v1:{self.manifest.provider_id}",
                request_id=str(uuid4()),
            ),
            page=page,
            quota=QuotaStatus(limit=1_000, remaining=999),
        )

    def _maybe_fail(self, capability: ProviderCapability) -> None:
        if capability not in self.manifest.capabilities:
            raise self._call_error(
                capability,
                ProviderErrorCode.UNSUPPORTED,
                "capability is unsupported",
                retryable=False,
            )
        code = self._failure_overrides.get(capability)
        if code is not None:
            raise self._call_error(
                capability,
                code,
                "recorded provider failure",
                retryable=code not in {
                    ProviderErrorCode.INVALID_REQUEST,
                    ProviderErrorCode.NOT_FOUND,
                },
            )

    def _call_error(
        self,
        capability: ProviderCapability,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool,
    ) -> ProviderCallError:
        return ProviderCallError(
            ProviderFailure(
                provider_id=self.manifest.provider_id,
                capability=capability,
                code=code,
                safe_message=message,
                retryable=retryable,
            )
        )


def _without(raw: dict[str, Any], *keys: str) -> dict[str, Any]:
    excluded = frozenset(keys)
    return {key: value for key, value in raw.items() if key not in excluded}


FixtureModel = TypeVar("FixtureModel", bound=BaseModel)
