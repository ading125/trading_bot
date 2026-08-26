"""Source-bounded AI evidence packaging, validation, caching, and polling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
from uuid import uuid4

from investing_bot.db import (
    AIAssessment,
    AnalysisEvidence,
    AnalysisEvidencePackage,
    AnalysisRepository,
    CandidateRepository,
    CandidateSourceType,
    JobRunRepository,
    MarketDataRepository,
)
from investing_bot.models import (
    AnalysisDecision,
    EvidenceItem,
    ProviderCapability,
    StructuredAnalysisRequest,
)
from investing_bot.providers import ProviderManager


logger = logging.getLogger(__name__)
PROMPT_VERSION = "growth_analysis.v2"
OUTPUT_SCHEMA_VERSION = "structured_analysis.v1"
JOB_TYPE_PREFIX = "ai_analysis"
DEFAULT_MAX_EVIDENCE = 12
DEFAULT_MAX_EVIDENCE_CHARS = 4_000
DEFAULT_MAX_EVIDENCE_PER_SOURCE_TYPE = 6
MAX_EVIDENCE_ITEM_CHARS = 1_000


class AnalysisError(RuntimeError):
    """Base error for an assessment that is safe not to publish."""


class AnalysisEvidenceError(AnalysisError):
    pass


class AnalysisValidationError(AnalysisError):
    pass


@dataclass(frozen=True, slots=True)
class AnalysisExecution:
    assessment: AIAssessment
    cached: bool


class AnalysisEvidenceBuilder:
    def __init__(
        self,
        candidates: CandidateRepository,
        *,
        market: MarketDataRepository | None = None,
        max_evidence: int = DEFAULT_MAX_EVIDENCE,
        max_evidence_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
        max_evidence_per_source_type: int = DEFAULT_MAX_EVIDENCE_PER_SOURCE_TYPE,
        now=lambda: datetime.now(UTC),
    ) -> None:
        if max_evidence < 1:
            raise ValueError("max_evidence must be positive")
        if max_evidence_chars < 1:
            raise ValueError("max_evidence_chars must be positive")
        if max_evidence_per_source_type < 1:
            raise ValueError("max_evidence_per_source_type must be positive")
        self.candidates = candidates
        self.market = market
        self.max_evidence = max_evidence
        self.max_evidence_chars = max_evidence_chars
        self.max_evidence_per_source_type = max_evidence_per_source_type
        self._now = now

    def build(self, symbol: str) -> AnalysisEvidencePackage:
        ticker = symbol.upper()
        candidate = self.candidates.get_candidate(ticker)
        if candidate is None:
            raise AnalysisEvidenceError(f"active candidate {ticker} was not found")
        source_rows = self.candidates.list_evidence(
            symbol=ticker, active_only=True, limit=500
        )
        momentum = self.market.momentum(ticker) if self.market is not None else None
        selected: list[AnalysisEvidence] = []
        seen_text: set[str] = set()
        source_type_counts: dict[CandidateSourceType, int] = {}
        remaining_chars = self.max_evidence_chars
        source_limit = self.max_evidence - (1 if momentum is not None else 0)
        for source in sorted(
            source_rows,
            key=lambda item: (item.relevance, item.observed_at, item.evidence_id),
            reverse=True,
        ):
            if len(selected) == source_limit or remaining_chars <= 0:
                break
            source_type_count = source_type_counts.get(source.source_type, 0)
            if source_type_count >= self.max_evidence_per_source_type:
                continue
            normalized = " ".join(source.source_excerpt.casefold().split())
            content_key = sha256(normalized.encode("utf-8")).hexdigest()
            if content_key in seen_text:
                continue
            text = source.source_excerpt[
                : min(MAX_EVIDENCE_ITEM_CHARS, remaining_chars)
            ]
            if not text.strip():
                continue
            seen_text.add(content_key)
            selected.append(
                AnalysisEvidence(
                    source_id=source.evidence_id,
                    source_type=source.source_type,
                    source_record_id=source.source_record_id,
                    text=text,
                    source_url=source.source_url,
                    event_at=source.event_at,
                    observed_at=source.observed_at,
                    relevance=source.relevance,
                )
            )
            source_type_counts[source.source_type] = source_type_count + 1
            remaining_chars -= len(text)
        if momentum is not None and remaining_chars > 0:
            momentum_text = (
                f"Adjusted daily price momentum over {momentum.session_count} sessions "
                f"from {momentum.start_session.isoformat()} to "
                f"{momentum.end_session.isoformat()}: close moved from "
                f"${momentum.start_close:.2f} to ${momentum.end_close:.2f} "
                f"({momentum.return_pct:+.1f}%), with a "
                f"{momentum.range_pct:.1f}% close-to-close range. Historical price "
                "movement is context, not a forecast."
            )[: min(MAX_EVIDENCE_ITEM_CHARS, remaining_chars)]
            record_id = (
                f"{ticker}:adjusted-daily:{momentum.start_session.isoformat()}:"
                f"{momentum.end_session.isoformat()}"
            )
            selected.append(
                AnalysisEvidence(
                    source_id=sha256(record_id.encode("utf-8")).hexdigest(),
                    source_type=CandidateSourceType.MARKET,
                    source_record_id=record_id,
                    text=momentum_text,
                    source_url=None,
                    event_at=momentum.observed_at,
                    observed_at=momentum.observed_at,
                    relevance=0.85,
                )
            )
        if not selected:
            raise AnalysisEvidenceError(f"candidate {ticker} has no active evidence")
        evidence_hash = _evidence_hash(
            ticker=ticker,
            company_name=candidate.company_name,
            evidence=tuple(selected),
        )
        return AnalysisEvidencePackage(
            evidence_hash=evidence_hash,
            ticker=ticker,
            company_name=candidate.company_name,
            prompt_version=PROMPT_VERSION,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            evidence=tuple(selected),
            created_at=self._now(),
        )


class GrowthAnalysisService:
    def __init__(
        self,
        *,
        evidence_builder: AnalysisEvidenceBuilder,
        repository: AnalysisRepository,
        provider_manager: ProviderManager,
        jobs: JobRunRepository,
        qualification_growth_score: int = 65,
        qualification_evidence_quality: int = 60,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.evidence_builder = evidence_builder
        self.repository = repository
        self.provider_manager = provider_manager
        self.jobs = jobs
        self.qualification_growth_score = qualification_growth_score
        self.qualification_evidence_quality = qualification_evidence_quality
        self._now = now

    async def analyze(self, symbol: str) -> AnalysisExecution:
        package = self.evidence_builder.build(symbol)
        owner = f"analysis-{package.ticker}-{uuid4()}"
        job_type = f"{JOB_TYPE_PREFIX}:{package.ticker}"
        if not self.jobs.acquire_lease(
            job_type=job_type,
            owner=owner,
            lease_duration=timedelta(minutes=10),
        ):
            raise AnalysisError(f"analysis for {package.ticker} is already running")
        run = self.jobs.create(
            job_type=job_type,
            code_version="0.1.0",
            config_hash=self.provider_manager.configuration.configuration_hash,
        )
        self.jobs.start(run.run_id, owner=owner)
        try:
            pinned = await self.provider_manager.pin(
                ProviderCapability.STRUCTURED_LLM, run_id=run.run_id
            )
            cache_key = _cache_key(
                evidence_hash=package.evidence_hash,
                prompt_version=package.prompt_version,
                output_schema_version=package.output_schema_version,
                provider_id=pinned.provider_id,
                model_id=pinned.manifest.model_id or pinned.provider_id,
                adapter_version=pinned.manifest.adapter_version,
                configuration_hash=pinned.configuration_hash,
            )
            cached = self.repository.find_cached(cache_key)
            if cached is not None:
                self.jobs.succeed(run.run_id, finished_at=self._now())
                return AnalysisExecution(assessment=cached, cached=True)

            self.repository.store_package(package)
            result = await pinned.analyze(
                StructuredAnalysisRequest(
                    ticker=package.ticker,
                    prompt_version=package.prompt_version,
                    output_schema_version=package.output_schema_version,
                    evidence=tuple(
                        EvidenceItem(
                            source_id=item.source_id,
                            text=item.text,
                            observed_at=item.observed_at,
                        )
                        for item in package.evidence
                    ),
                )
            )
            if len(result.items) != 1:
                raise AnalysisValidationError(
                    "analysis provider must return exactly one assessment"
                )
            output = result.items[0]
            self._validate_output(package, output)
            assessment = AIAssessment(
                assessment_id=str(uuid4()),
                cache_key=cache_key,
                evidence_hash=package.evidence_hash,
                ticker=package.ticker,
                company_name=package.company_name,
                decision=output.decision,
                growth_score=output.growth_score,
                evidence_quality=output.evidence_quality,
                policy_relevance=output.policy_relevance,
                catalysts=output.catalysts,
                earnings_assessment=output.earnings_assessment,
                bullish_thesis=output.bullish_thesis,
                bearish_case=output.bearish_case,
                risks=output.risks,
                uncertainties=output.uncertainties,
                source_ids=output.source_ids,
                prompt_version=package.prompt_version,
                output_schema_version=package.output_schema_version,
                provider_id=result.provenance.provider_id,
                model_id=pinned.manifest.model_id or pinned.provider_id,
                adapter_version=result.provenance.adapter_version,
                provider_request_id=result.provenance.request_id,
                configuration_hash=(result.provenance.configuration_hash or ""),
                input_tokens=(
                    result.usage.input_tokens if result.usage is not None else None
                ),
                output_tokens=(
                    result.usage.output_tokens if result.usage is not None else None
                ),
                created_at=self._now(),
            )
            self.repository.store_assessment(assessment)
            self.jobs.succeed(run.run_id, finished_at=assessment.created_at)
            return AnalysisExecution(assessment=assessment, cached=False)
        except Exception as exc:
            self.jobs.fail(run.run_id, error_summary=str(exc) or type(exc).__name__)
            raise
        finally:
            self.jobs.release_lease(job_type=job_type, owner=owner)

    def _validate_output(self, package, output) -> None:
        by_id = {item.source_id: item for item in package.evidence}
        cited = [by_id[source_id] for source_id in output.source_ids]
        if not cited:
            raise AnalysisValidationError(
                "an assessment must cite supplied evidence"
            )
        if output.decision is AnalysisDecision.QUALIFY:
            if output.growth_score < self.qualification_growth_score:
                raise AnalysisValidationError(
                    "qualify decision is below the growth-score threshold"
                )
            if output.evidence_quality < self.qualification_evidence_quality:
                raise AnalysisValidationError(
                    "qualify decision is below the evidence-quality threshold"
                )
            material_sources = {
                CandidateSourceType.NEWS,
                CandidateSourceType.EARNINGS,
            }
            if not any(item.source_type in material_sources for item in cited):
                raise AnalysisValidationError(
                    "political or membership evidence alone cannot qualify a company"
                )


class AnalysisPollingService:
    def __init__(
        self,
        service: GrowthAnalysisService,
        *,
        symbols: tuple[str, ...],
        interval_seconds: int,
        initial_delay_seconds: float = 2.0,
    ) -> None:
        self.service = service
        self.symbols = symbols
        self.interval_seconds = interval_seconds
        self.initial_delay_seconds = initial_delay_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="ai-analysis-polling")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(self.initial_delay_seconds)
        while True:
            for symbol in self.symbols:
                try:
                    execution = await self.service.analyze(symbol)
                    logger.info(
                        "AI analysis completed",
                        extra={
                            "ticker": symbol,
                            "assessment_id": execution.assessment.assessment_id,
                            "decision": execution.assessment.decision.value,
                            "cached": execution.cached,
                        },
                    )
                except AnalysisEvidenceError:
                    logger.info(
                        "AI analysis skipped because candidate evidence is unavailable",
                        extra={"ticker": symbol},
                    )
                except Exception:
                    logger.exception("AI analysis failed", extra={"ticker": symbol})
            await asyncio.sleep(self.interval_seconds)


def _evidence_hash(
    *, ticker: str, company_name: str, evidence: tuple[AnalysisEvidence, ...]
) -> str:
    payload = {
        "ticker": ticker,
        "company_name": company_name,
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _cache_key(**parts: str) -> str:
    return sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
