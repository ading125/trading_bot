"""Deterministic company resolution and expiring candidate union."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from importlib.resources import files
import json
import logging
import re
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from investing_bot.db import (
    CandidateEvidence,
    CandidateRepository,
    CandidateSourceType,
    CompanyAlias,
    EntityResolution,
    JobRunRepository,
    ResolutionStatus,
)
from investing_bot.models import ProviderCapability, SymbolLookupRequest
from investing_bot.providers import ProviderManager


logger = logging.getLogger(__name__)
ALIAS_PACKAGE = "investing_bot.services.data"
ALIAS_FILE = "company_aliases.v1.json"
JOB_TYPE = "candidate_refresh"
_LEGAL_SUFFIXES = re.compile(
    r"\b(?:incorporated|inc|corporation|corp|company|co|limited|ltd|llc)\.?$",
    re.IGNORECASE,
)
_COMPANY_PATTERN = re.compile(
    r"\b(?:[A-Z][\w&'.-]*\s+){0,5}"
    r"(?:[A-Z][\w&'.-]*\s*)"
    r"(?:Inc\.?|Corporation|Corp\.?|Company|Co\.?|LLC|Ltd\.?|Holdings|Group|Technologies)\b"
)
_TICKER_PATTERN = re.compile(r"(?<!\w)\$([A-Z][A-Z0-9.-]{0,11})\b")


class SuggestedEntityType(StrEnum):
    COMPANY = "company"
    PERSON = "person"
    PLACE = "place"
    AGENCY = "agency"
    INDUSTRY = "industry"
    UNKNOWN = "unknown"


class OrganizationSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=300)
    entity_type: SuggestedEntityType
    confidence: float = Field(ge=0, le=1)
    suggested_symbol: str | None = Field(
        default=None, pattern=r"^[A-Z][A-Z0-9.-]{0,11}$"
    )


class OrganizationExtractionFallback(Protocol):
    async def extract(
        self, text: str, *, max_suggestions: int
    ) -> tuple[OrganizationSuggestion, ...]: ...


class NoopOrganizationExtractionFallback:
    async def extract(
        self, text: str, *, max_suggestions: int
    ) -> tuple[OrganizationSuggestion, ...]:
        return ()


class ExtractedMention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    normalized: str
    method: str
    entity_type: SuggestedEntityType = SuggestedEntityType.COMPANY
    suggested_symbol: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    grounded: bool = True


class CandidateRefreshSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    sources_scanned: int
    resolutions_recorded: int
    candidates_active: int
    evidence_active: int
    manual_review_count: int
    recorded_at: datetime


def normalize_entity_name(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    value = _LEGAL_SUFFIXES.sub("", value).strip()
    return " ".join(value.split())


def load_packaged_aliases(*, verified_at: datetime) -> tuple[CompanyAlias, ...]:
    payload = json.loads(files(ALIAS_PACKAGE).joinpath(ALIAS_FILE).read_text("utf-8"))
    version = str(payload["version"])
    return tuple(
        CompanyAlias(
            alias=item["alias"],
            normalized_alias=normalize_entity_name(item["alias"]),
            symbol=item["symbol"],
            company_name=item["company_name"],
            alias_version=version,
            source="packaged",
            verified_at=verified_at,
        )
        for item in payload["aliases"]
    )


class DeterministicOrganizationExtractor:
    def extract(
        self, text: str, aliases: tuple[CompanyAlias, ...]
    ) -> tuple[ExtractedMention, ...]:
        matches: list[tuple[int, int, ExtractedMention]] = []
        occupied: list[tuple[int, int]] = []
        unique_aliases: dict[str, CompanyAlias] = {}
        for alias in aliases:
            unique_aliases.setdefault(alias.alias.casefold(), alias)
        for alias in sorted(unique_aliases.values(), key=lambda item: -len(item.alias)):
            pattern = re.compile(
                rf"(?<![A-Za-z0-9]){re.escape(alias.alias)}(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            for found in pattern.finditer(text):
                if _overlaps(found.span(), occupied):
                    continue
                occupied.append(found.span())
                matches.append(
                    (
                        found.start(),
                        found.end(),
                        ExtractedMention(
                            text=found.group(0),
                            normalized=alias.normalized_alias,
                            method="alias",
                        ),
                    )
                )
        for found in _TICKER_PATTERN.finditer(text):
            if _overlaps(found.span(), occupied):
                continue
            occupied.append(found.span())
            matches.append(
                (
                    found.start(),
                    found.end(),
                    ExtractedMention(
                        text=found.group(1),
                        normalized=found.group(1).casefold(),
                        method="explicit_ticker",
                        suggested_symbol=found.group(1),
                    ),
                )
            )
        for found in _COMPANY_PATTERN.finditer(text):
            if _overlaps(found.span(), occupied):
                continue
            candidate = found.group(0).strip()
            normalized = normalize_entity_name(candidate)
            if not normalized:
                continue
            occupied.append(found.span())
            matches.append(
                (
                    found.start(),
                    found.end(),
                    ExtractedMention(
                        text=candidate,
                        normalized=normalized,
                        method="company_suffix",
                    ),
                )
            )
        return tuple(item[2] for item in sorted(matches, key=lambda item: item[0]))


class CompanyResolver:
    def __init__(
        self,
        *,
        repository: CandidateRepository,
        provider_manager: ProviderManager,
        fallback: OrganizationExtractionFallback | None = None,
        extractor: DeterministicOrganizationExtractor | None = None,
        max_source_characters: int = 8_000,
        max_fallback_suggestions: int = 8,
        minimum_confidence: float = 0.80,
        ambiguity_margin: float = 0.05,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repository = repository
        self.provider_manager = provider_manager
        self.fallback = fallback or NoopOrganizationExtractionFallback()
        self.extractor = extractor or DeterministicOrganizationExtractor()
        self.max_source_characters = max_source_characters
        self.max_fallback_suggestions = max_fallback_suggestions
        self.minimum_confidence = minimum_confidence
        self.ambiguity_margin = ambiguity_margin
        self._now = now

    async def resolve_text(
        self,
        *,
        source_type: CandidateSourceType,
        source_record_id: str,
        text: str,
        run_id: str,
    ) -> tuple[EntityResolution, ...]:
        bounded = " ".join(text.split())[: self.max_source_characters]
        aliases = self.repository.list_active_aliases()
        mentions = list(self.extractor.extract(bounded, aliases))
        if not mentions:
            suggestions = await self.fallback.extract(
                bounded, max_suggestions=self.max_fallback_suggestions
            )
            if len(suggestions) > self.max_fallback_suggestions:
                suggestions = suggestions[: self.max_fallback_suggestions]
            normalized_source = normalize_entity_name(bounded)
            mentions = []
            for suggestion in suggestions:
                normalized = normalize_entity_name(suggestion.name)
                mentions.append(
                    ExtractedMention(
                        text=suggestion.name,
                        normalized=normalized,
                        method="ai_fallback",
                        entity_type=suggestion.entity_type,
                        suggested_symbol=suggestion.suggested_symbol,
                        confidence=suggestion.confidence,
                        grounded=bool(normalized and normalized in normalized_source),
                    )
                )
        results: list[EntityResolution] = []
        for mention in _unique_mentions(mentions):
            result = await self._resolve_mention(
                mention=mention,
                source_type=source_type,
                source_record_id=source_record_id,
                source_text=bounded,
                run_id=run_id,
            )
            self.repository.store_resolution(result)
            results.append(result)
        return tuple(results)

    async def _resolve_mention(
        self,
        *,
        mention: ExtractedMention,
        source_type: CandidateSourceType,
        source_record_id: str,
        source_text: str,
        run_id: str,
    ) -> EntityResolution:
        common = {
            "resolution_id": _stable_id(
                "resolution", source_type.value, source_record_id, mention.normalized
            ),
            "source_type": source_type,
            "source_record_id": source_record_id,
            "mention_text": mention.text,
            "normalized_mention": mention.normalized,
            "source_excerpt": _excerpt(source_text, mention.text),
            "extraction_method": mention.method,
            "resolved_at": self._now(),
        }
        if not mention.grounded:
            return EntityResolution(
                **common,
                status=ResolutionStatus.REJECTED,
                symbol=None,
                company_name=None,
                confidence=0,
                alias_version=None,
                provider_id=None,
                provider_request_id=None,
                alternatives=(),
                reason="extracted entity is not present in the source text",
            )
        if mention.entity_type is not SuggestedEntityType.COMPANY:
            return EntityResolution(
                **common,
                status=ResolutionStatus.REJECTED,
                symbol=None,
                company_name=None,
                confidence=mention.confidence,
                alias_version=None,
                provider_id=None,
                provider_request_id=None,
                alternatives=(),
                reason=f"entity type {mention.entity_type.value} is not a company",
            )
        aliases = self.repository.lookup_aliases(mention.normalized)
        by_symbol = {alias.symbol: alias for alias in aliases}
        if len(by_symbol) == 1:
            alias = next(iter(by_symbol.values()))
            return EntityResolution(
                **common,
                status=ResolutionStatus.RESOLVED,
                symbol=alias.symbol,
                company_name=alias.company_name,
                confidence=1.0,
                alias_version=alias.alias_version,
                provider_id=None,
                provider_request_id=None,
                alternatives=(),
                reason=None,
            )
        if len(by_symbol) > 1:
            return EntityResolution(
                **common,
                status=ResolutionStatus.AMBIGUOUS,
                symbol=None,
                company_name=None,
                confidence=0,
                alias_version=None,
                provider_id=None,
                provider_request_id=None,
                alternatives=tuple(
                    {"symbol": alias.symbol, "company_name": alias.company_name}
                    for alias in by_symbol.values()
                ),
                reason="alias maps to multiple active symbols",
            )
        pinned = await self.provider_manager.pin(
            ProviderCapability.SYMBOL_LOOKUP, run_id=run_id
        )
        provider_result = await pinned.lookup_symbols(
            SymbolLookupRequest(query=mention.text, us_listed_only=True)
        )
        matches = sorted(
            (
                item
                for item in provider_result.items
                if item.active and item.quote_type.casefold() == "equity"
            ),
            key=lambda item: item.confidence,
            reverse=True,
        )
        alternatives = tuple(
            {
                "symbol": item.symbol,
                "company_name": item.company_name,
                "confidence": item.confidence,
            }
            for item in matches[:5]
        )
        if mention.suggested_symbol and not any(
            item.symbol == mention.suggested_symbol for item in matches
        ):
            return EntityResolution(
                **common,
                status=ResolutionStatus.UNRESOLVED,
                symbol=None,
                company_name=None,
                confidence=0,
                alias_version=None,
                provider_id=pinned.provider_id,
                provider_request_id=provider_result.provenance.request_id,
                alternatives=alternatives,
                reason="suggested ticker was not verified by the symbol provider",
            )
        if not matches or matches[0].confidence < self.minimum_confidence:
            return EntityResolution(
                **common,
                status=ResolutionStatus.UNRESOLVED,
                symbol=None,
                company_name=None,
                confidence=matches[0].confidence if matches else 0,
                alias_version=None,
                provider_id=pinned.provider_id,
                provider_request_id=provider_result.provenance.request_id,
                alternatives=alternatives,
                reason="no sufficiently confident US-listed equity match",
            )
        if (
            len(matches) > 1
            and matches[0].symbol != matches[1].symbol
            and matches[0].confidence - matches[1].confidence <= self.ambiguity_margin
        ):
            return EntityResolution(
                **common,
                status=ResolutionStatus.AMBIGUOUS,
                symbol=None,
                company_name=None,
                confidence=matches[0].confidence,
                alias_version=None,
                provider_id=pinned.provider_id,
                provider_request_id=provider_result.provenance.request_id,
                alternatives=alternatives,
                reason="multiple provider matches are too close to choose safely",
            )
        selected = matches[0]
        return EntityResolution(
            **common,
            status=ResolutionStatus.RESOLVED,
            symbol=selected.symbol,
            company_name=selected.company_name,
            confidence=selected.confidence,
            alias_version=None,
            provider_id=pinned.provider_id,
            provider_request_id=provider_result.provenance.request_id,
            alternatives=alternatives,
            reason=None,
        )


class CandidateRegistryService:
    def __init__(
        self,
        *,
        repository: CandidateRepository,
        resolver: CompanyResolver,
        jobs: JobRunRepository,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repository = repository
        self.resolver = resolver
        self.jobs = jobs
        self._now = now

    def analysis_queue(self, *, limit: int) -> tuple[str, ...]:
        """Return the strongest current non-universe-only research candidates."""

        return self.repository.list_analysis_symbols(limit=limit)

    async def refresh(self) -> CandidateRefreshSummary:
        owner = f"candidates-{uuid4()}"
        if not self.jobs.acquire_lease(
            job_type=JOB_TYPE,
            owner=owner,
            lease_duration=timedelta(minutes=10),
        ):
            raise RuntimeError("candidate refresh is already running")
        run = self.jobs.create(
            job_type=JOB_TYPE,
            code_version="0.1.0",
            config_hash=self.resolver.provider_manager.configuration.configuration_hash,
        )
        self.jobs.start(run.run_id, owner=owner)
        try:
            now = self._now()
            self.repository.store_aliases(load_packaged_aliases(verified_at=now))
            sources = self.repository.discovery_sources()
            resolutions = manual_review = 0
            seen_evidence: set[str] = set()
            discovered_aliases: list[CompanyAlias] = []
            for source in sources:
                source_type = source["source_type"]
                if source_type is CandidateSourceType.SP500:
                    discovered_aliases.append(
                        CompanyAlias(
                            alias=str(source["company_name"]),
                            normalized_alias=normalize_entity_name(
                                str(source["company_name"])
                            ),
                            symbol=str(source["symbol"]),
                            company_name=str(source["company_name"]),
                            alias_version=str(source["source_record_id"]).split(":", 1)[
                                0
                            ],
                            source="sp500_snapshot",
                            verified_at=source["observed_at"],
                        )
                    )
            self.repository.store_aliases(tuple(discovered_aliases))

            direct_evidence = tuple(
                self._build_direct_source(source, now=now)
                for source in sources
                if source["symbol"] is not None
            )
            self.repository.store_evidence_batch(direct_evidence)
            seen_evidence.update(item.evidence_id for item in direct_evidence)

            for source in sources:
                if source["symbol"] is not None:
                    continue
                source_type = source["source_type"]
                resolved = await self.resolver.resolve_text(
                    source_type=source_type,
                    source_record_id=str(source["source_record_id"]),
                    text=str(source["text"]),
                    run_id=run.run_id,
                )
                resolutions += len(resolved)
                manual_review += sum(
                    item.status in {ResolutionStatus.AMBIGUOUS, ResolutionStatus.UNRESOLVED}
                    for item in resolved
                )
                for item in resolved:
                    if item.status is ResolutionStatus.RESOLVED:
                        seen_evidence.add(
                            self._store_resolved_source(source, item=item, now=now)
                        )
            self.repository.deactivate_evidence_not_seen(seen_evidence)
            active, evidence = self.repository.refresh_candidate_state(now=now)
            summary = CandidateRefreshSummary(
                run_id=run.run_id,
                sources_scanned=len(sources),
                resolutions_recorded=resolutions,
                candidates_active=active,
                evidence_active=evidence,
                manual_review_count=manual_review,
                recorded_at=now,
            )
            self.repository.record_refresh_summary(**summary.model_dump())
            self.jobs.succeed(run.run_id, finished_at=now)
            return summary
        except Exception as exc:
            self.jobs.fail(run.run_id, error_summary=str(exc) or type(exc).__name__)
            raise
        finally:
            self.jobs.release_lease(job_type=JOB_TYPE, owner=owner)

    def _build_direct_source(
        self, source: dict[str, object], *, now: datetime
    ) -> CandidateEvidence:
        source_type = source["source_type"]
        assert isinstance(source_type, CandidateSourceType)
        symbol = str(source["symbol"])
        raw_company_name = str(source["company_name"] or symbol)
        company_name = (
            self.repository.company_name_for_symbol(symbol)
            if raw_company_name == symbol
            else raw_company_name
        ) or symbol
        evidence_id = _stable_id(
            "evidence", source_type.value, str(source["source_record_id"]), symbol
        )
        return CandidateEvidence(
            evidence_id=evidence_id,
            symbol=symbol,
            company_name=company_name,
            source_type=source_type,
            source_record_id=str(source["source_record_id"]),
            source_excerpt=str(source["text"])[:1_000],
            source_url=source["url"],
            event_at=source["event_at"],
            observed_at=source["observed_at"],
            extraction_method="source_symbol",
            resolution_id=None,
            resolution_confidence=1.0,
            relevance=_relevance(source_type),
            expires_at=_expiry(source_type, source["observed_at"]),
            active=_expiry(source_type, source["observed_at"]) > now,
        )

    def _store_resolved_source(
        self,
        source: dict[str, object],
        *,
        item: EntityResolution,
        now: datetime,
    ) -> str:
        assert item.symbol is not None and item.company_name is not None
        source_type = source["source_type"]
        assert isinstance(source_type, CandidateSourceType)
        expires_at = _expiry(source_type, source["observed_at"])
        evidence_id = _stable_id(
            "evidence",
            source_type.value,
            str(source["source_record_id"]),
            item.symbol,
        )
        self.repository.store_evidence(
            CandidateEvidence(
                evidence_id=evidence_id,
                symbol=item.symbol,
                company_name=item.company_name,
                source_type=source_type,
                source_record_id=str(source["source_record_id"]),
                source_excerpt=item.source_excerpt,
                source_url=source["url"],
                event_at=source["event_at"],
                observed_at=source["observed_at"],
                extraction_method=item.extraction_method,
                resolution_id=item.resolution_id,
                resolution_confidence=item.confidence,
                relevance=min(item.confidence, _relevance(source_type)),
                expires_at=expires_at,
                active=expires_at > now,
            )
        )
        return evidence_id


class CandidatePollingService:
    def __init__(
        self,
        service: CandidateRegistryService,
        *,
        interval_seconds: int,
        initial_delay_seconds: float = 0.5,
    ) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self.initial_delay_seconds = initial_delay_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="candidate-refresh")

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
        # Let the ASGI lifespan yield so Uvicorn binds its socket before the
        # first potentially expensive refresh begins.
        await asyncio.sleep(self.initial_delay_seconds)
        while True:
            try:
                summary = await self.service.refresh()
                logger.info(
                    "candidate refresh completed",
                    extra=summary.model_dump(mode="json"),
                )
            except Exception:
                logger.exception("candidate refresh failed")
            await asyncio.sleep(self.interval_seconds)


def _expiry(source_type: CandidateSourceType, observed_at: datetime) -> datetime:
    days = {
        CandidateSourceType.SP500: 14,
        CandidateSourceType.CIVICTRACKER: 30,
        CandidateSourceType.NEWS: 14,
        CandidateSourceType.EARNINGS: 30,
    }[source_type]
    return observed_at + timedelta(days=days)


def _relevance(source_type: CandidateSourceType) -> float:
    return {
        CandidateSourceType.SP500: 0.50,
        CandidateSourceType.CIVICTRACKER: 0.60,
        CandidateSourceType.NEWS: 0.90,
        CandidateSourceType.EARNINGS: 0.85,
    }[source_type]


def _stable_id(*parts: str) -> str:
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _excerpt(source: str, mention: str, *, limit: int = 500) -> str:
    location = source.casefold().find(mention.casefold())
    if location < 0:
        return source[:limit]
    start = max(0, location - limit // 2)
    return source[start : start + limit]


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in occupied)


def _unique_mentions(
    mentions: list[ExtractedMention],
) -> tuple[ExtractedMention, ...]:
    result: list[ExtractedMention] = []
    seen: set[str] = set()
    for mention in mentions:
        if not mention.normalized or mention.normalized in seen:
            continue
        seen.add(mention.normalized)
        result.append(mention)
    return tuple(result)
