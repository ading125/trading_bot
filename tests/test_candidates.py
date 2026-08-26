from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from investing_bot.db import (
    CandidateEvidence,
    CandidateRepository,
    CandidateSourceType,
    CompanyAlias,
    Database,
    JobRunRepository,
    MarketDataRepository,
    ResolutionStatus,
    SocialPostRepository,
)
from investing_bot.models import (
    BarInterval,
    CanonicalMetadata,
    EarningsRequest,
    MarketBar,
    NewsRequest,
    PriceAdjustment,
    ProviderCapability,
    SocialPostsRequest,
)
from investing_bot.providers import (
    CapabilitySelection,
    CredentialPresenceStore,
    ProviderConfiguration,
    ProviderManager,
    ProviderRegistry,
    ProviderTarget,
)
from investing_bot.providers.fixtures import RecordedFixtureProvider, build_fixture_manifest
from investing_bot.services import (
    CandidateRegistryService,
    CompanyResolver,
    OrganizationSuggestion,
    SuggestedEntityType,
    load_packaged_aliases,
    normalize_entity_name,
)


NOW = datetime(2026, 8, 14, 14, 0, tzinfo=UTC)
MEMBER_ID = "3094abf7-4a95-4b8d-8c8d-af7d1c3747a1"


def setup_candidates(
    tmp_path: Path,
) -> tuple[
    Database,
    CandidateRepository,
    ProviderManager,
    RecordedFixtureProvider,
]:
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    assert database.migrate() == 11
    repository = CandidateRepository(database)
    provider = RecordedFixtureProvider("fixture_recorded")
    registry = ProviderRegistry()
    registry.register(build_fixture_manifest("fixture_recorded"), lambda: provider)
    manager = ProviderManager(
        registry=registry,
        configuration=ProviderConfiguration(
            selections={
                ProviderCapability.SYMBOL_LOOKUP: CapabilitySelection(
                    primary=ProviderTarget(provider_id="fixture_recorded")
                )
            }
        ),
        credentials=CredentialPresenceStore(),
    )
    return database, repository, manager, provider


@pytest.mark.anyio
async def test_chevron_post_resolves_with_exact_source_passage(
    tmp_path: Path,
) -> None:
    database, repository, manager, provider = setup_candidates(tmp_path)
    posts = await provider.fetch_social_posts(
        SocialPostsRequest(member_id=MEMBER_ID, page_size=20)
    )
    social_repository = SocialPostRepository(database)
    for post in posts.items:
        social_repository.store(post, official_uuid=MEMBER_ID)
    resolver = CompanyResolver(
        repository=repository, provider_manager=manager, now=lambda: NOW
    )
    service = CandidateRegistryService(
        repository=repository,
        resolver=resolver,
        jobs=JobRunRepository(database),
        now=lambda: NOW,
    )

    summary = await service.refresh()

    candidates = repository.list_candidates()
    resolutions = repository.list_resolutions()
    evidence = repository.list_evidence(symbol="CVX")
    assert summary.candidates_active == 1
    assert [(item.symbol, item.company_name) for item in candidates] == [
        ("CVX", "Chevron Corporation")
    ]
    assert resolutions[0].status is ResolutionStatus.RESOLVED
    assert resolutions[0].extraction_method == "alias"
    assert "Chairman and CEO of Chevron" in resolutions[0].source_excerpt
    assert evidence[0].source_type is CandidateSourceType.CIVICTRACKER
    assert evidence[0].source_excerpt == resolutions[0].source_excerpt

    database.execute(
        "UPDATE social_posts SET discovery_eligible=false WHERE post_id='653387'"
    )
    await service.refresh()
    assert repository.list_candidates() == []
    assert repository.list_evidence(symbol="CVX") == []
    database.close()


@pytest.mark.anyio
async def test_direct_sources_form_a_union_and_expire_without_refresh(
    tmp_path: Path,
) -> None:
    database, repository, manager, provider = setup_candidates(tmp_path)
    market = MarketDataRepository(database, dataset_root=tmp_path / "market")
    market.save_universe_snapshot(
        snapshot_id="snapshot-1",
        source_url="https://example.com/sp500",
        captured_at=NOW,
        raw_payload_hash="a" * 64,
        members=(
            {
                "symbol": "CVX",
                "company_name": "Chevron Corporation",
                "sector": "Energy",
                "sub_industry": "Integrated Oil & Gas",
            },
            {
                "symbol": "MSFT",
                "company_name": "Microsoft Corporation",
                "sector": "Information Technology",
                "sub_industry": "Systems Software",
            },
        ),
    )
    market.store_news(
        (
            await provider.fetch_news(NewsRequest(symbols=("CVX",)))
        ).items
    )
    market.store_earnings(
        (
            await provider.fetch_earnings(EarningsRequest(symbols=("CVX",)))
        ).items
    )
    service = CandidateRegistryService(
        repository=repository,
        resolver=CompanyResolver(
            repository=repository, provider_manager=manager, now=lambda: NOW
        ),
        jobs=JobRunRepository(database),
        now=lambda: NOW,
    )

    await service.refresh()

    candidate = repository.list_candidates()[0]
    evidence = repository.list_evidence(symbol="CVX")
    assert candidate.symbol == "CVX"
    assert candidate.company_name == "Chevron Corporation"
    assert candidate.source_count == 3
    assert {item.source_type for item in evidence} == {
        CandidateSourceType.SP500,
        CandidateSourceType.NEWS,
        CandidateSourceType.EARNINGS,
    }
    assert repository.list_analysis_symbols() == ("CVX",)

    active, active_evidence = repository.refresh_candidate_state(
        now=NOW + timedelta(days=31)
    )
    assert (active, active_evidence) == (0, 0)
    assert repository.list_candidates() == []
    assert repository.list_candidates(active_only=False)[0].active is False
    database.close()


def test_analysis_queue_prioritizes_fresh_company_activity_and_excludes_spy(
    tmp_path: Path,
) -> None:
    database, repository, _, _ = setup_candidates(tmp_path)
    now = datetime.now(UTC)
    evidence: list[CandidateEvidence] = []

    def add(
        symbol: str,
        company: str,
        source_type: CandidateSourceType,
        index: int,
        *,
        event_at: datetime,
    ) -> None:
        evidence.append(
            CandidateEvidence(
                evidence_id=f"{symbol}-{source_type.value}-{index}",
                symbol=symbol,
                company_name=company,
                source_type=source_type,
                source_record_id=f"record-{symbol}-{source_type.value}-{index}",
                source_excerpt="Material company development.",
                source_url="https://example.com/source",
                event_at=event_at,
                observed_at=now,
                extraction_method="source_symbol",
                resolution_id=None,
                resolution_confidence=1.0,
                relevance=0.9,
                expires_at=now + timedelta(days=14),
                active=True,
            )
        )

    for index in range(5):
        add("SPY", "SPDR S&P 500 ETF Trust", CandidateSourceType.NEWS, index, event_at=now)
    for index in range(3):
        add("TREND", "Trending Company", CandidateSourceType.NEWS, index, event_at=now)
    add("MIX", "Mixed Evidence Company", CandidateSourceType.NEWS, 0, event_at=now)
    add("MIX", "Mixed Evidence Company", CandidateSourceType.EARNINGS, 0, event_at=now)
    add(
        "MOVE",
        "High Momentum Company",
        CandidateSourceType.NEWS,
        0,
        event_at=now - timedelta(days=5),
    )
    for index in range(4):
        add(
            "OLD",
            "Older News Company",
            CandidateSourceType.NEWS,
            index,
            event_at=now - timedelta(days=5),
        )

    repository.store_evidence_batch(tuple(evidence))
    repository.refresh_candidate_state(now=now)
    market = MarketDataRepository(database, dataset_root=tmp_path / "market")
    market.store_bars(
        tuple(
            _daily_bar(
                "MOVE",
                date(2026, 5, 1) + timedelta(days=index),
                100.0 + index * 5.0,
            )
            for index in range(20)
        )
    )

    assert repository.list_analysis_symbols(limit=10) == (
        "MOVE",
        "TREND",
        "MIX",
        "OLD",
    )
    database.close()


def test_repeated_large_alias_snapshot_is_an_immutable_noop(tmp_path: Path) -> None:
    database, repository, _, _ = setup_candidates(tmp_path)
    aliases = tuple(
        CompanyAlias(
            alias=f"Company {index}",
            normalized_alias=f"company {index}",
            symbol=f"S{index}",
            company_name=f"Company {index}",
            alias_version="snapshot-1",
            source="sp500_snapshot",
            verified_at=NOW,
        )
        for index in range(600)
    )

    assert repository.store_aliases(aliases) == 600
    assert repository.store_aliases(aliases) == 600
    assert database.fetchone("SELECT COUNT(*) FROM company_aliases") == (600,)
    database.close()


@pytest.mark.anyio
async def test_ambiguous_alias_never_silently_selects_a_ticker(
    tmp_path: Path,
) -> None:
    database, repository, manager, _ = setup_candidates(tmp_path)
    repository.store_aliases(
        tuple(
            CompanyAlias(
                alias="Acme",
                normalized_alias=normalize_entity_name("Acme"),
                symbol=symbol,
                company_name=company,
                alias_version="test.v1",
                source="test",
                verified_at=NOW,
            )
            for symbol, company in (("ACME", "Acme Corp"), ("ACMX", "Acme Holdings"))
        )
    )
    resolver = CompanyResolver(
        repository=repository, provider_manager=manager, now=lambda: NOW
    )

    result = await resolver.resolve_text(
        source_type=CandidateSourceType.CIVICTRACKER,
        source_record_id="post-ambiguous",
        text="Acme announced a new project.",
        run_id="resolution-run",
    )

    assert len(result) == 1
    assert result[0].status is ResolutionStatus.AMBIGUOUS
    assert result[0].symbol is None
    assert {item["symbol"] for item in result[0].alternatives} == {"ACME", "ACMX"}
    assert repository.list_candidates() == []
    database.close()


class FixtureFallback:
    async def extract(
        self, text: str, *, max_suggestions: int
    ) -> tuple[OrganizationSuggestion, ...]:
        return (
            OrganizationSuggestion(
                name="Donald Trump",
                entity_type=SuggestedEntityType.PERSON,
                confidence=0.99,
            ),
            OrganizationSuggestion(
                name="California",
                entity_type=SuggestedEntityType.PLACE,
                confidence=0.99,
            ),
            OrganizationSuggestion(
                name="Department of Energy",
                entity_type=SuggestedEntityType.AGENCY,
                confidence=0.99,
            ),
            OrganizationSuggestion(
                name="oil industry",
                entity_type=SuggestedEntityType.INDUSTRY,
                confidence=0.99,
            ),
            OrganizationSuggestion(
                name="Mystery",
                entity_type=SuggestedEntityType.COMPANY,
                confidence=0.95,
                suggested_symbol="ZZZZ",
            ),
        )


@pytest.mark.anyio
async def test_fallback_rejects_non_companies_and_unverified_ai_tickers(
    tmp_path: Path,
) -> None:
    database, repository, manager, _ = setup_candidates(tmp_path)
    repository.store_aliases(load_packaged_aliases(verified_at=NOW))
    resolver = CompanyResolver(
        repository=repository,
        provider_manager=manager,
        fallback=FixtureFallback(),
        now=lambda: NOW,
    )

    result = await resolver.resolve_text(
        source_type=CandidateSourceType.CIVICTRACKER,
        source_record_id="post-ai",
        text=(
            "Donald Trump discussed California, the Department of Energy, "
            "the oil industry, and Mystery."
        ),
        run_id="fallback-run",
    )

    assert len(result) == 5
    assert sum(item.status is ResolutionStatus.REJECTED for item in result) == 4
    unknown = next(item for item in result if item.mention_text == "Mystery")
    assert unknown.status is ResolutionStatus.UNRESOLVED
    assert unknown.symbol is None
    assert "not verified" in unknown.reason
    assert repository.list_candidates() == []
    database.close()


@pytest.mark.anyio
async def test_explicit_ticker_is_verified_by_symbol_provider(tmp_path: Path) -> None:
    database, repository, manager, _ = setup_candidates(tmp_path)
    resolver = CompanyResolver(
        repository=repository, provider_manager=manager, now=lambda: NOW
    )

    result = await resolver.resolve_text(
        source_type=CandidateSourceType.CIVICTRACKER,
        source_record_id="post-ticker",
        text="$CVX reported an operational update.",
        run_id="ticker-run",
    )

    assert result[0].status is ResolutionStatus.RESOLVED
    assert result[0].symbol == "CVX"
    assert result[0].provider_id == "fixture_recorded"
    assert result[0].provider_request_id
    database.close()


def _daily_bar(symbol: str, session: date, close: float) -> MarketBar:
    bar_start = datetime.combine(session, time(13, 30), UTC)
    bar_end = datetime.combine(session, time(20), UTC)
    identity = f"{symbol}:{session}:{close}"
    return MarketBar(
        symbol=symbol,
        interval=BarInterval.DAY_1,
        bar_start=bar_start,
        bar_end=bar_end,
        session_date=session,
        exchange_timezone="America/New_York",
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1_000_000,
        adjustment=PriceAdjustment.ADJUSTED,
        metadata=CanonicalMetadata(
            provider_id="fixture_recorded",
            provider_record_id=identity,
            event_at=bar_end,
            known_available_at=bar_end,
            retrieved_at=bar_end,
            raw_payload_hash=sha256(identity.encode()).hexdigest(),
            schema_version="market_bar.v1",
            adapter_version="1.0.0",
            dataset_lineage="fixture_recorded:adjusted:daily",
        ),
    )
