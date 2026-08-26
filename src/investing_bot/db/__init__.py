"""DuckDB persistence services and migration support."""

from investing_bot.db.database import Database, DatabaseError, MigrationError
from investing_bot.db.jobs import JobRun, JobRunRepository, JobStatus
from investing_bot.db.civictracker import (
    CollectionCheckpoint,
    CollectionSummary,
    PostWriteKind,
    SocialPostRepository,
    StoredSocialPost,
)
from investing_bot.db.market import (
    MarketDataRepository,
    MarketDataset,
    MarketMomentum,
    MarketStatus,
    MarketWriteKind,
)
from investing_bot.db.candidates import (
    Candidate,
    CandidateEvidence,
    CandidateRepository,
    CandidateSourceType,
    CompanyAlias,
    EntityResolution,
    ResolutionStatus,
)
from investing_bot.db.analysis import (
    AIAssessment,
    AnalysisEvidence,
    AnalysisEvidencePackage,
    AnalysisOutcome,
    AnalysisRepository,
    PendingAnalysisOutcome,
)
from investing_bot.db.strategy import StoredStrategyEvaluation, StrategyRepository
from investing_bot.db.backtest import (
    BacktestRunSummary,
    BacktestRepository,
    StoredBacktestExperiment,
    StoredBacktestRun,
)
from investing_bot.db.operations import OperationsRepository

__all__ = [
    "Database",
    "DatabaseError",
    "CollectionCheckpoint",
    "CollectionSummary",
    "JobRun",
    "JobRunRepository",
    "JobStatus",
    "MigrationError",
    "MarketDataRepository",
    "MarketDataset",
    "MarketMomentum",
    "MarketStatus",
    "MarketWriteKind",
    "Candidate",
    "CandidateEvidence",
    "CandidateRepository",
    "CandidateSourceType",
    "CompanyAlias",
    "EntityResolution",
    "ResolutionStatus",
    "AIAssessment",
    "BacktestRepository",
    "BacktestRunSummary",
    "AnalysisEvidence",
    "AnalysisEvidencePackage",
    "AnalysisOutcome",
    "AnalysisRepository",
    "PendingAnalysisOutcome",
    "StoredStrategyEvaluation",
    "StoredBacktestExperiment",
    "StoredBacktestRun",
    "StrategyRepository",
    "OperationsRepository",
    "PostWriteKind",
    "SocialPostRepository",
    "StoredSocialPost",
]
