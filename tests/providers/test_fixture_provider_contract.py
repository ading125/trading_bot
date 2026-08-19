from __future__ import annotations

from datetime import UTC, datetime

import pytest

from investing_bot.models import (
    BarInterval,
    BarsRequest,
    CorporateActionsRequest,
    EarningsRequest,
    EvidenceItem,
    NewsRequest,
    PriceAdjustment,
    ProviderCapability,
    QuotesRequest,
    SocialPostsRequest,
    StructuredAnalysisRequest,
    SymbolLookupRequest,
)
from investing_bot.providers import (
    CapabilitySelection,
    CredentialPresenceStore,
    ProviderConfiguration,
    ProviderCallError,
    ProviderManager,
    ProviderRegistry,
    ProviderTarget,
)
from investing_bot.providers.contracts import ProviderContractError, ProviderErrorCode
from investing_bot.providers.fixtures import (
    RecordedFixtureProvider,
    build_fixture_manifest,
    load_recorded_payload,
)


START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 8, 15, tzinfo=UTC)
MEMBER_ID = "3094abf7-4a95-4b8d-8c8d-af7d1c3747a1"


def manager_for(provider_id: str, capability: ProviderCapability) -> ProviderManager:
    registry = ProviderRegistry()
    registry.register(
        build_fixture_manifest(provider_id),
        lambda: RecordedFixtureProvider(provider_id),
    )
    return ProviderManager(
        registry=registry,
        configuration=ProviderConfiguration(
            selections={
                capability: CapabilitySelection(
                    primary=ProviderTarget(provider_id=provider_id)
                )
            }
        ),
        credentials=CredentialPresenceStore(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("provider_id", ["fixture_recorded", "fixture_backup"])
async def test_daily_bar_contract_preserves_adjustment_session_and_lineage(
    provider_id: str,
) -> None:
    manager = manager_for(provider_id, ProviderCapability.DAILY_BARS)
    pinned = await manager.pin(ProviderCapability.DAILY_BARS, run_id="daily-run")

    raw = await pinned.fetch_bars(
        BarsRequest(
            symbols=("SPY",),
            start=START,
            end=END,
            interval=BarInterval.DAY_1,
            adjustment=PriceAdjustment.RAW,
        )
    )
    adjusted = await pinned.fetch_bars(
        BarsRequest(
            symbols=("SPY",),
            start=START,
            end=END,
            interval=BarInterval.DAY_1,
            adjustment=PriceAdjustment.ADJUSTED,
        )
    )

    assert raw.items[0].adjustment is PriceAdjustment.RAW
    assert adjusted.items[0].adjustment is PriceAdjustment.ADJUSTED
    assert raw.items[0].close != adjusted.items[0].close
    assert raw.items[0].exchange_timezone == "America/New_York"
    assert raw.items[0].bar_end.utcoffset().total_seconds() == 0
    assert raw.provenance.provider_id == provider_id
    assert raw.provenance.run_id == "daily-run"
    assert raw.provenance.configuration_hash == manager.configuration.configuration_hash
    assert raw.items[0].metadata.dataset_lineage == raw.provenance.dataset_lineage
    assert raw.quota is not None and raw.quota.remaining == 999
    assert pinned.manifest.rate_limit is not None
    assert pinned.manifest.rate_limit.requests == 1_000


@pytest.mark.anyio
async def test_intraday_quote_and_corporate_action_contracts() -> None:
    intraday = manager_for("fixture_recorded", ProviderCapability.INTRADAY_BARS)
    bars = await (
        await intraday.pin(ProviderCapability.INTRADAY_BARS, run_id="intraday")
    ).fetch_bars(
        BarsRequest(
            symbols=("CVX",),
            start=START,
            end=END,
            interval=BarInterval.MINUTE_15,
            adjustment=PriceAdjustment.RAW,
        )
    )
    assert len(bars.items) == 1
    assert (bars.items[0].bar_end - bars.items[0].bar_start).total_seconds() == 900

    quotes = manager_for("fixture_recorded", ProviderCapability.QUOTES)
    quote_result = await (
        await quotes.pin(ProviderCapability.QUOTES, run_id="quotes")
    ).fetch_quotes(QuotesRequest(symbols=("CVX",)))
    assert quote_result.items[0].bid <= quote_result.items[0].ask

    actions = manager_for("fixture_recorded", ProviderCapability.CORPORATE_ACTIONS)
    action_result = await (
        await actions.pin(ProviderCapability.CORPORATE_ACTIONS, run_id="actions")
    ).fetch_corporate_actions(
        CorporateActionsRequest(symbols=("CVX",), start=START, end=END)
    )
    assert action_result.items[0].cash_amount == 1.71


@pytest.mark.anyio
async def test_symbol_news_and_earnings_contracts() -> None:
    symbols = manager_for("fixture_recorded", ProviderCapability.SYMBOL_LOOKUP)
    symbol_result = await (
        await symbols.pin(ProviderCapability.SYMBOL_LOOKUP, run_id="symbols")
    ).lookup_symbols(SymbolLookupRequest(query="Chevron"))
    assert [(item.symbol, item.active) for item in symbol_result.items] == [("CVX", True)]

    news = manager_for("fixture_recorded", ProviderCapability.NEWS)
    news_result = await (
        await news.pin(ProviderCapability.NEWS, run_id="news")
    ).fetch_news(NewsRequest(symbols=("CVX",)))
    assert news_result.items[0].metadata.known_available_at >= news_result.items[0].published_at

    earnings = manager_for("fixture_recorded", ProviderCapability.EARNINGS)
    earnings_result = await (
        await earnings.pin(ProviderCapability.EARNINGS, run_id="earnings")
    ).fetch_earnings(EarningsRequest(symbols=("CVX",)))
    assert earnings_result.items[0].reported_eps > earnings_result.items[0].estimated_eps
    assert (
        earnings_result.items[0].metadata.known_available_at
        > earnings_result.items[0].metadata.event_at
    )


@pytest.mark.anyio
async def test_social_pagination_is_idempotent_and_has_a_known_boundary() -> None:
    manager = manager_for("fixture_recorded", ProviderCapability.SOCIAL_POSTS)
    pinned = await manager.pin(ProviderCapability.SOCIAL_POSTS, run_id="social")
    request = SocialPostsRequest(member_id=MEMBER_ID, page_size=2)

    first = await pinned.fetch_social_posts(request)
    repeated = await pinned.fetch_social_posts(request)
    second = await pinned.fetch_social_posts(
        SocialPostsRequest(
            member_id=MEMBER_ID,
            cursor=first.page.next_cursor if first.page else None,
            page_size=2,
        )
    )

    assert [item.content_hash for item in first.items] == [
        item.content_hash for item in repeated.items
    ]
    assert first.page is not None and first.page.has_more
    assert first.page.next_cursor == "2"
    assert second.page is not None and not second.page.has_more
    assert second.page.next_cursor is None
    assert len(first.items) + len(second.items) == 3


@pytest.mark.anyio
async def test_structured_analysis_output_is_source_bounded() -> None:
    manager = manager_for("fixture_recorded", ProviderCapability.STRUCTURED_LLM)
    pinned = await manager.pin(ProviderCapability.STRUCTURED_LLM, run_id="llm")
    result = await pinned.analyze(
        StructuredAnalysisRequest(
            ticker="CVX",
            prompt_version="growth_analysis.v1",
            output_schema_version="structured_analysis.v1",
            evidence=(
                EvidenceItem(
                    source_id="news-cvx-1",
                    text="Recorded evidence only.",
                    observed_at=datetime(2026, 8, 14, 12, 30, tzinfo=UTC),
                ),
            ),
        )
    )

    assert result.items[0].ticker == "CVX"
    assert result.items[0].source_ids == ("news-cvx-1",)
    assert 0 <= result.items[0].growth_score <= 100
    assert result.provenance.schema_version == "structured_analysis.v1"

    with pytest.raises(ProviderContractError, match="output schema"):
        await pinned.analyze(
            StructuredAnalysisRequest(
                ticker="CVX",
                prompt_version="growth_analysis.v1",
                output_schema_version="different_analysis.v1",
                evidence=(
                    EvidenceItem(
                        source_id="news-cvx-1",
                        text="Recorded evidence only.",
                        observed_at=datetime(2026, 8, 14, 12, 30, tzinfo=UTC),
                    ),
                ),
            )
        )


@pytest.mark.anyio
async def test_schema_and_request_failures_map_to_sanitized_errors() -> None:
    payload = load_recorded_payload()
    payload["bars"][0]["low"] = 999.0
    provider = RecordedFixtureProvider("fixture_invalid_schema", payload=payload)

    with pytest.raises(ProviderCallError) as schema_error:
        await provider.fetch_bars(
            BarsRequest(
                symbols=("SPY",),
                start=START,
                end=END,
                interval=BarInterval.DAY_1,
                adjustment=PriceAdjustment.RAW,
            )
        )
    assert schema_error.value.failure.code is ProviderErrorCode.SCHEMA_INCOMPATIBLE
    assert "999" not in schema_error.value.failure.safe_message

    with pytest.raises(ProviderCallError) as cursor_error:
        await provider.fetch_social_posts(
            SocialPostsRequest(member_id=MEMBER_ID, cursor="not-a-cursor")
        )
    assert cursor_error.value.failure.code is ProviderErrorCode.INVALID_REQUEST
    assert cursor_error.value.failure.retryable is False
