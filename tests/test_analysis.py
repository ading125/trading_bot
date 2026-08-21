from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from investing_bot.db import (
    AnalysisRepository,
    CandidateEvidence,
    CandidateRepository,
    CandidateSourceType,
    Database,
    JobRunRepository,
)
from investing_bot.models import AnalysisDecision, ProviderCapability
from investing_bot.providers import (
    CapabilitySelection,
    CredentialPresenceStore,
    ProviderConfiguration,
    ProviderManager,
    ProviderRegistry,
    ProviderTarget,
)
from investing_bot.providers.fixtures import (
    RecordedFixtureProvider,
    build_fixture_manifest,
    load_recorded_payload,
)
from investing_bot.services import (
    AnalysisEvidenceBuilder,
    AnalysisValidationError,
    GrowthAnalysisService,
)


NOW = datetime(2026, 8, 14, 12, 30, tzinfo=UTC)


def setup_analysis(
    tmp_path: Path,
    *,
    source_type: CandidateSourceType = CandidateSourceType.NEWS,
    payload: dict | None = None,
) -> tuple[
    Database,
    CandidateRepository,
    AnalysisRepository,
    GrowthAnalysisService,
    RecordedFixtureProvider,
]:
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    assert database.migrate() == 6
    candidates = CandidateRepository(database)
    candidates.store_evidence(
        CandidateEvidence(
            evidence_id="news-cvx-1",
            symbol="CVX",
            company_name="Chevron Corporation",
            source_type=source_type,
            source_record_id="news-cvx-1",
            source_excerpt="Chevron reported a production outlook update.",
            source_url="https://example.test/news-cvx-1",
            event_at=NOW,
            observed_at=NOW,
            extraction_method="source_symbol",
            resolution_id=None,
            resolution_confidence=1,
            relevance=0.9,
            expires_at=NOW + timedelta(days=30),
            active=True,
        )
    )
    candidates.refresh_candidate_state(now=NOW)

    capability = frozenset({ProviderCapability.STRUCTURED_LLM})
    provider = RecordedFixtureProvider(
        "fixture_recorded", payload=payload, capabilities=capability
    )
    registry = ProviderRegistry()
    registry.register(
        build_fixture_manifest("fixture_recorded", capabilities=capability),
        lambda: provider,
    )
    manager = ProviderManager(
        registry=registry,
        configuration=ProviderConfiguration(
            selections={
                ProviderCapability.STRUCTURED_LLM: CapabilitySelection(
                    primary=ProviderTarget(provider_id="fixture_recorded")
                )
            }
        ),
        credentials=CredentialPresenceStore(),
    )
    analyses = AnalysisRepository(database)
    service = GrowthAnalysisService(
        evidence_builder=AnalysisEvidenceBuilder(candidates, now=lambda: NOW),
        repository=analyses,
        provider_manager=manager,
        jobs=JobRunRepository(database),
        now=lambda: NOW,
    )
    return database, candidates, analyses, service, provider


@pytest.mark.anyio
async def test_analysis_is_source_bounded_persisted_and_cached(tmp_path: Path) -> None:
    database, _, repository, service, provider = setup_analysis(tmp_path)

    first = await service.analyze("cvx")
    assert first.cached is False
    assert first.assessment.decision is AnalysisDecision.INVESTIGATE
    assert first.assessment.source_ids == ("news-cvx-1",)
    package = repository.get_package(first.assessment.evidence_hash)
    assert package is not None
    assert package.evidence[0].text == (
        "Chevron reported a production outlook update."
    )
    assert [item.horizon_sessions for item in repository.outcomes(
        first.assessment.assessment_id
    )] == [5, 10, 20]

    async def unexpected_call(request):
        raise AssertionError("unchanged evidence should not call the provider")

    provider.analyze = unexpected_call
    second = await service.analyze("CVX")
    assert second.cached is True
    assert second.assessment.assessment_id == first.assessment.assessment_id
    assert repository.count() == 1
    database.close()


@pytest.mark.anyio
async def test_changed_evidence_creates_assessment_history(tmp_path: Path) -> None:
    database, candidates, repository, service, _ = setup_analysis(tmp_path)
    first = await service.analyze("CVX")
    candidates.store_evidence(
        CandidateEvidence(
            evidence_id="earnings-cvx-2",
            symbol="CVX",
            company_name="Chevron Corporation",
            source_type=CandidateSourceType.EARNINGS,
            source_record_id="earnings-cvx-2",
            source_excerpt="Chevron published a later earnings update.",
            source_url="https://example.test/earnings-cvx-2",
            event_at=NOW + timedelta(hours=1),
            observed_at=NOW + timedelta(hours=1),
            extraction_method="source_symbol",
            resolution_id=None,
            resolution_confidence=1,
            relevance=0.95,
            expires_at=NOW + timedelta(days=30),
            active=True,
        )
    )

    second = await service.analyze("CVX")

    assert second.cached is False
    assert second.assessment.evidence_hash != first.assessment.evidence_hash
    assert len(repository.history("CVX")) == 2
    database.close()


@pytest.mark.anyio
async def test_political_evidence_alone_cannot_publish_qualify(tmp_path: Path) -> None:
    payload = deepcopy(load_recorded_payload())
    payload["analyses"][0].update(
        {
            "decision": "qualify",
            "growth_score": 90,
            "evidence_quality": 90,
            "source_ids": ["news-cvx-1"],
        }
    )
    database, _, repository, service, _ = setup_analysis(
        tmp_path,
        source_type=CandidateSourceType.CIVICTRACKER,
        payload=payload,
    )

    with pytest.raises(AnalysisValidationError, match="cannot qualify"):
        await service.analyze("CVX")

    assert repository.count() == 0
    database.close()
