from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from investing_bot.db import (
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
    EarningsRequest,
    NewsRequest,
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
    assert database.migrate() == 6
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

    active, active_evidence = repository.refresh_candidate_state(
        now=NOW + timedelta(days=31)
    )
    assert (active, active_evidence) == (0, 0)
    assert repository.list_candidates() == []
    assert repository.list_candidates(active_only=False)[0].active is False
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
