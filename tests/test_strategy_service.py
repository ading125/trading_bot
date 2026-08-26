from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from investing_bot.db import (
    AIAssessment,
    AnalysisRepository,
    CandidateEvidence,
    CandidateRepository,
    CandidateSourceType,
    Database,
    JobRunRepository,
    MarketDataRepository,
    StrategyRepository,
)
from investing_bot.models import (
    AnalysisDecision,
    BarInterval,
    CanonicalMetadata,
    MarketBar,
    PolicyRelevance,
    PriceAdjustment,
    SetupState,
)
from investing_bot.services import StrategyEvaluationService
from investing_bot.strategies import build_default_strategy_registry


NOW = datetime(2026, 8, 14, 12, 30, tzinfo=UTC)
BAR_START = datetime(2026, 6, 20, 14, 30, tzinfo=UTC)


def _market_bar(
    symbol: str,
    *,
    index: int,
    close: float,
    volume: int,
) -> MarketBar:
    bar_start = BAR_START + timedelta(days=index)
    bar_end = bar_start + timedelta(hours=6, minutes=30)
    identity = f"{symbol}:{index}:{close}:{volume}"
    return MarketBar(
        symbol=symbol,
        interval=BarInterval.DAY_1,
        bar_start=bar_start,
        bar_end=bar_end,
        session_date=bar_start.date(),
        exchange_timezone="America/New_York",
        open=close - 0.2,
        high=close + 0.4,
        low=close - 0.6,
        close=close,
        volume=volume,
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


def _qualified_candidate(
    candidates: CandidateRepository,
    analyses: AnalysisRepository,
) -> None:
    candidates.store_evidence(
        CandidateEvidence(
            evidence_id="news-cvx-strategy",
            symbol="CVX",
            company_name="Chevron Corporation",
            source_type=CandidateSourceType.NEWS,
            source_record_id="news-cvx-strategy",
            source_excerpt="Chevron published a material growth update.",
            source_url="https://example.test/news-cvx-strategy",
            event_at=NOW,
            observed_at=NOW,
            extraction_method="source_symbol",
            resolution_id=None,
            resolution_confidence=1,
            relevance=0.95,
            expires_at=NOW + timedelta(days=90),
            active=True,
        )
    )
    candidates.refresh_candidate_state(now=NOW)
    analyses.store_assessment(
        AIAssessment(
            assessment_id="assessment-cvx-strategy",
            cache_key="cache-cvx-strategy",
            evidence_hash="e" * 64,
            ticker="CVX",
            company_name="Chevron Corporation",
            decision=AnalysisDecision.QUALIFY,
            growth_score=85,
            evidence_quality=90,
            policy_relevance=PolicyRelevance.NONE,
            catalysts=("Capacity expansion",),
            earnings_assessment="Supported by the supplied evidence.",
            bullish_thesis="A source-bounded growth thesis.",
            bearish_case="Execution could disappoint.",
            risks=("Commodity prices",),
            uncertainties=("Timing",),
            source_ids=("news-cvx-strategy",),
            prompt_version="growth.v1",
            output_schema_version="analysis.v1",
            provider_id="fixture_recorded",
            model_id="fixture-analysis",
            adapter_version="1.0.0",
            provider_request_id="request-cvx-strategy",
            configuration_hash="c" * 64,
            created_at=NOW,
        )
    )


def test_strategy_service_persists_caches_and_preserves_stale_state(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    assert database.migrate() == 11
    candidates = CandidateRepository(database)
    analyses = AnalysisRepository(database)
    market = MarketDataRepository(database, dataset_root=tmp_path / "market")
    repository = StrategyRepository(database)
    _qualified_candidate(candidates, analyses)

    bars: list[MarketBar] = []
    for index in range(61):
        candidate_close = 132.0 if index == 60 else 100 + index * 0.5
        bars.append(
            _market_bar(
                "CVX",
                index=index,
                close=candidate_close,
                volume=2_000_000 if index == 60 else 1_000_000,
            )
        )
        bars.append(
            _market_bar(
                "SPY",
                index=index,
                close=100 + index * 0.1,
                volume=1_000_000,
            )
        )
    market.store_bars(tuple(bars))

    as_of = bars[-2].bar_end + timedelta(hours=1)
    future_bar = _market_bar("CVX", index=90, close=500, volume=9_000_000)
    market.store_bars((future_bar,))
    visible = market.strategy_bars(
        provider_id="fixture_recorded",
        symbol="CVX",
        interval=BarInterval.DAY_1,
        as_of=as_of,
        limit=400,
    )
    assert len(visible) == 61
    assert visible[-1].close == 132.0

    service = StrategyEvaluationService(
        market=market,
        candidates=candidates,
        analyses=analyses,
        strategies=build_default_strategy_registry(),
        repository=repository,
        jobs=JobRunRepository(database),
        now=lambda: NOW,
    )
    first = service.evaluate_symbol("cvx", as_of=as_of)
    second = service.evaluate_symbol("CVX", as_of=as_of)

    assert first.eligible_for_strategy is True
    assert len(first.evaluations) == 2
    breakout = next(
        item for item in first.evaluations if item.signal.strategy_id == "breakout"
    )
    assert breakout.signal.state is SetupState.CONFIRMED
    assert breakout.research_status.value == "hypothesis"
    assert second.cached_evaluations == 2
    assert repository.count() == 2
    assert repository.latest("CVX", "breakout") == breakout

    stale = service.evaluate_symbol("CVX", as_of=as_of + timedelta(days=10))
    stale_breakout = next(
        item for item in stale.evaluations if item.signal.strategy_id == "breakout"
    )
    assert stale_breakout.signal.state is SetupState.CONFIRMED
    assert stale_breakout.signal.stale is True
    assert stale_breakout.signal.confirmation_blocked is True
    assert "preserved the prior confirmed state" in stale_breakout.signal.explanation.summary
    assert len(repository.history("CVX", strategy_id="breakout")) == 2
    assert repository.count() == 4
    database.close()


def test_strategy_service_requires_ai_qualification(tmp_path: Path) -> None:
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    database.migrate()
    candidates = CandidateRepository(database)
    candidates.store_evidence(
        CandidateEvidence(
            evidence_id="news-cvx-unqualified",
            symbol="CVX",
            company_name="Chevron Corporation",
            source_type=CandidateSourceType.NEWS,
            source_record_id="news-cvx-unqualified",
            source_excerpt="A candidate without a qualifying AI assessment.",
            source_url=None,
            event_at=NOW,
            observed_at=NOW,
            extraction_method="source_symbol",
            resolution_id=None,
            resolution_confidence=1,
            relevance=0.8,
            expires_at=NOW + timedelta(days=90),
            active=True,
        )
    )
    candidates.refresh_candidate_state(now=NOW)
    service = StrategyEvaluationService(
        market=MarketDataRepository(database, dataset_root=tmp_path / "market"),
        candidates=candidates,
        analyses=AnalysisRepository(database),
        strategies=build_default_strategy_registry(),
        repository=StrategyRepository(database),
        jobs=JobRunRepository(database),
        now=lambda: NOW,
    )

    result = service.evaluate_symbol("CVX", as_of=NOW)

    assert result.eligible_for_strategy is False
    assert result.reason == "current_ai_assessment_required"
    database.close()
