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
)
from investing_bot.db.strategy import StoredStrategyEvaluation, StrategyRepository

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
    "AnalysisEvidence",
    "AnalysisEvidencePackage",
    "AnalysisOutcome",
    "AnalysisRepository",
    "StoredStrategyEvaluation",
    "StrategyRepository",
    "PostWriteKind",
    "SocialPostRepository",
    "StoredSocialPost",
]
