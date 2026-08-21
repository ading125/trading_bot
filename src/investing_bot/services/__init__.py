"""Application services that operate on canonical domain models."""

from investing_bot.services.source_post_deduplicator import (
    DeduplicationOutcome,
    DeduplicationResult,
    SourcePostDeduplicator,
)

from investing_bot.services.civictracker_collector import (
    CivicTrackerCollector,
    CivicTrackerPollingService,
    CollectionBusyError,
)
from investing_bot.services.market_data import (
    MarketCollectionSummary,
    MarketDataCollector,
    MarketPollingService,
    MarketDataValidationError,
    MarketDataValidator,
)
from investing_bot.services.sp500 import (
    SP500Member,
    SP500Snapshot,
    SP500UniverseCollector,
    parse_sp500_html,
)
from investing_bot.services.candidates import (
    CandidatePollingService,
    CandidateRefreshSummary,
    CandidateRegistryService,
    CompanyResolver,
    DeterministicOrganizationExtractor,
    NoopOrganizationExtractionFallback,
    OrganizationExtractionFallback,
    OrganizationSuggestion,
    SuggestedEntityType,
    load_packaged_aliases,
    normalize_entity_name,
)
from investing_bot.services.analysis import (
    AnalysisError,
    AnalysisEvidenceBuilder,
    AnalysisEvidenceError,
    AnalysisExecution,
    AnalysisPollingService,
    AnalysisValidationError,
    GrowthAnalysisService,
)
from investing_bot.services.strategy import (
    StrategyEvaluationService,
    StrategyEvaluationSummary,
    StrategyPollingService,
)
from investing_bot.services.backtest import (
    BacktestExecution,
    BacktestResearchError,
    BacktestResearchService,
    ExperimentExecution,
)

__all__ = [
    "CivicTrackerCollector",
    "CivicTrackerPollingService",
    "CollectionBusyError",
    "CandidatePollingService",
    "CandidateRefreshSummary",
    "CandidateRegistryService",
    "AnalysisError",
    "AnalysisEvidenceBuilder",
    "AnalysisEvidenceError",
    "AnalysisExecution",
    "AnalysisPollingService",
    "AnalysisValidationError",
    "GrowthAnalysisService",
    "StrategyEvaluationService",
    "StrategyEvaluationSummary",
    "StrategyPollingService",
    "BacktestExecution",
    "BacktestResearchError",
    "BacktestResearchService",
    "ExperimentExecution",
    "CompanyResolver",
    "DeterministicOrganizationExtractor",
    "NoopOrganizationExtractionFallback",
    "OrganizationExtractionFallback",
    "OrganizationSuggestion",
    "SuggestedEntityType",
    "MarketCollectionSummary",
    "MarketDataCollector",
    "MarketPollingService",
    "MarketDataValidationError",
    "MarketDataValidator",
    "SP500Member",
    "SP500Snapshot",
    "SP500UniverseCollector",
    "DeduplicationOutcome",
    "DeduplicationResult",
    "SourcePostDeduplicator",
    "parse_sp500_html",
    "load_packaged_aliases",
    "normalize_entity_name",
]
