"""Persistence models for source-bounded AI assessments and outcomes."""

from __future__ import annotations

from datetime import date, datetime
import json

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from investing_bot.db.candidates import CandidateSourceType
from investing_bot.db.database import Database
from investing_bot.models import AnalysisDecision, PolicyRelevance


class AnalysisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    source_type: CandidateSourceType
    source_record_id: str
    text: str = Field(min_length=1, max_length=20_000)
    source_url: str | None
    event_at: AwareDatetime
    observed_at: AwareDatetime
    relevance: float = Field(ge=0, le=1)


class AnalysisEvidencePackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_hash: str
    ticker: str
    company_name: str
    prompt_version: str
    output_schema_version: str
    evidence: tuple[AnalysisEvidence, ...]
    created_at: AwareDatetime


class AIAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment_id: str
    cache_key: str
    evidence_hash: str
    ticker: str
    company_name: str
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
    prompt_version: str
    output_schema_version: str
    provider_id: str
    model_id: str
    adapter_version: str
    provider_request_id: str
    configuration_hash: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    created_at: AwareDatetime


class AnalysisOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment_id: str
    horizon_sessions: int
    baseline_session_date: date | None
    baseline_close: float | None
    outcome_session_date: date | None
    outcome_close: float | None
    return_pct: float | None
    recorded_at: AwareDatetime | None


class AnalysisRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def store_package(self, package: AnalysisEvidencePackage) -> None:
        evidence_json = json.dumps(
            [item.model_dump(mode="json") for item in package.evidence],
            sort_keys=True,
            separators=(",", ":"),
        )
        self._database.execute(
            """
            INSERT INTO analysis_evidence_packages VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (evidence_hash) DO NOTHING
            """,
            [
                package.evidence_hash,
                package.ticker,
                package.company_name,
                package.prompt_version,
                package.output_schema_version,
                evidence_json,
                package.created_at,
            ],
        )

    def get_package(self, evidence_hash: str) -> AnalysisEvidencePackage | None:
        row = self._database.fetchone(
            """
            SELECT evidence_hash, ticker, company_name, prompt_version,
                   output_schema_version, evidence_json, created_at
            FROM analysis_evidence_packages WHERE evidence_hash=?
            """,
            [evidence_hash],
        )
        if row is None:
            return None
        return AnalysisEvidencePackage(
            evidence_hash=row[0],
            ticker=row[1],
            company_name=row[2],
            prompt_version=row[3],
            output_schema_version=row[4],
            evidence=tuple(
                AnalysisEvidence.model_validate(item) for item in json.loads(row[5])
            ),
            created_at=row[6],
        )

    def find_cached(self, cache_key: str) -> AIAssessment | None:
        row = self._database.fetchone(
            f"SELECT {_ASSESSMENT_COLUMNS} FROM ai_assessments WHERE cache_key=?",
            [cache_key],
        )
        return None if row is None else _assessment_from_row(row)

    def store_assessment(self, assessment: AIAssessment) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO ai_assessments (
                    assessment_id, cache_key, evidence_hash, ticker, company_name,
                    decision, growth_score, evidence_quality, policy_relevance,
                    catalysts_json, earnings_assessment, bullish_thesis,
                    bearish_case, risks_json, uncertainties_json, source_ids_json,
                    prompt_version, output_schema_version, provider_id, model_id,
                    adapter_version, provider_request_id, configuration_hash,
                    input_tokens, output_tokens, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    assessment.assessment_id,
                    assessment.cache_key,
                    assessment.evidence_hash,
                    assessment.ticker,
                    assessment.company_name,
                    assessment.decision.value,
                    assessment.growth_score,
                    assessment.evidence_quality,
                    assessment.policy_relevance.value,
                    _json_tuple(assessment.catalysts),
                    assessment.earnings_assessment,
                    assessment.bullish_thesis,
                    assessment.bearish_case,
                    _json_tuple(assessment.risks),
                    _json_tuple(assessment.uncertainties),
                    _json_tuple(assessment.source_ids),
                    assessment.prompt_version,
                    assessment.output_schema_version,
                    assessment.provider_id,
                    assessment.model_id,
                    assessment.adapter_version,
                    assessment.provider_request_id,
                    assessment.configuration_hash,
                    assessment.input_tokens,
                    assessment.output_tokens,
                    assessment.created_at,
                ],
            )
            connection.executemany(
                "INSERT INTO analysis_outcomes (assessment_id, horizon_sessions) VALUES (?, ?)",
                [(assessment.assessment_id, horizon) for horizon in (5, 10, 20)],
            )

    def latest(self, ticker: str) -> AIAssessment | None:
        row = self._database.fetchone(
            f"""
            SELECT {_ASSESSMENT_COLUMNS} FROM ai_assessments
            WHERE ticker=? ORDER BY created_at DESC LIMIT 1
            """,
            [ticker.upper()],
        )
        return None if row is None else _assessment_from_row(row)

    def list_latest(self, *, limit: int = 20) -> list[AIAssessment]:
        rows = self._database.fetchall(
            f"""
            SELECT {_ASSESSMENT_COLUMNS} FROM ai_assessments
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ticker ORDER BY created_at DESC
            ) = 1
            ORDER BY created_at DESC LIMIT ?
            """,
            [limit],
        )
        return [_assessment_from_row(row) for row in rows]

    def history(self, ticker: str, *, limit: int = 50) -> list[AIAssessment]:
        rows = self._database.fetchall(
            f"""
            SELECT {_ASSESSMENT_COLUMNS} FROM ai_assessments
            WHERE ticker=? ORDER BY created_at DESC LIMIT ?
            """,
            [ticker.upper(), limit],
        )
        return [_assessment_from_row(row) for row in rows]

    def outcomes(self, assessment_id: str) -> list[AnalysisOutcome]:
        rows = self._database.fetchall(
            """
            SELECT assessment_id, horizon_sessions, baseline_session_date,
                   baseline_close, outcome_session_date, outcome_close,
                   return_pct, recorded_at
            FROM analysis_outcomes WHERE assessment_id=? ORDER BY horizon_sessions
            """,
            [assessment_id],
        )
        fields = tuple(AnalysisOutcome.model_fields)
        return [AnalysisOutcome(**dict(zip(fields, row, strict=True))) for row in rows]

    def count(self) -> int:
        row = self._database.fetchone("SELECT COUNT(*) FROM ai_assessments")
        return 0 if row is None else int(row[0])


_ASSESSMENT_COLUMNS = """
assessment_id, cache_key, evidence_hash, ticker, company_name, decision,
growth_score, evidence_quality, policy_relevance, catalysts_json,
earnings_assessment, bullish_thesis, bearish_case, risks_json,
uncertainties_json, source_ids_json, prompt_version, output_schema_version,
provider_id, model_id, adapter_version, provider_request_id, configuration_hash,
input_tokens, output_tokens, created_at
"""


def _json_tuple(items: tuple[str, ...]) -> str:
    return json.dumps(items, separators=(",", ":"))


def _assessment_from_row(row: tuple[object, ...]) -> AIAssessment:
    values = dict(zip(_ASSESSMENT_FIELDS, row, strict=True))
    for name in ("catalysts", "risks", "uncertainties", "source_ids"):
        values[name] = tuple(json.loads(values[name]))
    return AIAssessment(**values)


_ASSESSMENT_FIELDS = (
    "assessment_id",
    "cache_key",
    "evidence_hash",
    "ticker",
    "company_name",
    "decision",
    "growth_score",
    "evidence_quality",
    "policy_relevance",
    "catalysts",
    "earnings_assessment",
    "bullish_thesis",
    "bearish_case",
    "risks",
    "uncertainties",
    "source_ids",
    "prompt_version",
    "output_schema_version",
    "provider_id",
    "model_id",
    "adapter_version",
    "provider_request_id",
    "configuration_hash",
    "input_tokens",
    "output_tokens",
    "created_at",
)
