"""Candidate, alias, resolution, and source-evidence persistence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import json

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from investing_bot.db.database import Database


class CandidateSourceType(StrEnum):
    SP500 = "sp500"
    CIVICTRACKER = "civictracker"
    NEWS = "news"
    EARNINGS = "earnings"
    MARKET = "market"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    REJECTED = "rejected"


class CompanyAlias(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    alias: str
    normalized_alias: str
    symbol: str
    company_name: str
    alias_version: str
    source: str
    verified_at: AwareDatetime
    active: bool = True


class EntityResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolution_id: str
    source_type: CandidateSourceType
    source_record_id: str
    mention_text: str
    normalized_mention: str
    source_excerpt: str
    status: ResolutionStatus
    symbol: str | None
    company_name: str | None
    confidence: float = Field(ge=0, le=1)
    extraction_method: str
    alias_version: str | None
    provider_id: str | None
    provider_request_id: str | None
    alternatives: tuple[dict[str, object], ...]
    reason: str | None
    resolved_at: AwareDatetime


class CandidateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    symbol: str
    company_name: str
    source_type: CandidateSourceType
    source_record_id: str
    source_excerpt: str
    source_url: str | None
    event_at: AwareDatetime
    observed_at: AwareDatetime
    extraction_method: str
    resolution_id: str | None
    resolution_confidence: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    expires_at: AwareDatetime
    active: bool


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    company_name: str
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    expires_at: AwareDatetime
    highest_confidence: float = Field(ge=0, le=1)
    active: bool
    updated_at: AwareDatetime
    source_count: int


class CandidateRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def store_aliases(self, aliases: tuple[CompanyAlias, ...]) -> int:
        if not aliases:
            return 0
        parameters = [
            [
                alias.normalized_alias,
                alias.alias,
                alias.symbol,
                alias.company_name,
                alias.alias_version,
                alias.source,
                alias.verified_at,
                alias.active,
            ]
            for alias in aliases
        ]
        with self._database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO company_aliases VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                parameters,
            )
        return len(aliases)

    def lookup_aliases(self, normalized_alias: str) -> tuple[CompanyAlias, ...]:
        rows = self._database.fetchall(
            """
            SELECT alias, normalized_alias, symbol, company_name, alias_version,
                   source, verified_at, active
            FROM company_aliases
            WHERE normalized_alias=? AND active=true
            ORDER BY verified_at DESC, symbol
            """,
            [normalized_alias],
        )
        fields = tuple(CompanyAlias.model_fields)
        return tuple(
            CompanyAlias(**dict(zip(fields, row, strict=True))) for row in rows
        )

    def list_active_aliases(self) -> tuple[CompanyAlias, ...]:
        rows = self._database.fetchall(
            """
            SELECT alias, normalized_alias, symbol, company_name, alias_version,
                   source, verified_at, active
            FROM company_aliases WHERE active=true
            ORDER BY length(normalized_alias) DESC, normalized_alias
            """
        )
        fields = tuple(CompanyAlias.model_fields)
        return tuple(
            CompanyAlias(**dict(zip(fields, row, strict=True))) for row in rows
        )

    def store_resolution(self, item: EntityResolution) -> None:
        self._database.execute(
            """
            INSERT INTO entity_resolutions VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (source_type, source_record_id, normalized_mention)
            DO UPDATE SET
                resolution_id=excluded.resolution_id,
                mention_text=excluded.mention_text,
                source_excerpt=excluded.source_excerpt,
                status=excluded.status,
                symbol=excluded.symbol,
                company_name=excluded.company_name,
                confidence=excluded.confidence,
                extraction_method=excluded.extraction_method,
                alias_version=excluded.alias_version,
                provider_id=excluded.provider_id,
                provider_request_id=excluded.provider_request_id,
                alternatives_json=excluded.alternatives_json,
                reason=excluded.reason,
                resolved_at=excluded.resolved_at
            """,
            [
                item.resolution_id,
                item.source_type.value,
                item.source_record_id,
                item.mention_text,
                item.normalized_mention,
                item.source_excerpt,
                item.status.value,
                item.symbol,
                item.company_name,
                item.confidence,
                item.extraction_method,
                item.alias_version,
                item.provider_id,
                item.provider_request_id,
                json.dumps(item.alternatives, separators=(",", ":"), sort_keys=True),
                item.reason,
                item.resolved_at,
            ],
        )

    def store_evidence(self, item: CandidateEvidence) -> None:
        self.store_evidence_batch((item,))

    def store_evidence_batch(self, items: tuple[CandidateEvidence, ...]) -> int:
        if not items:
            return 0
        parameters = [
            [
                item.evidence_id,
                item.symbol,
                item.company_name,
                item.source_type.value,
                item.source_record_id,
                item.source_excerpt,
                item.source_url,
                item.event_at,
                item.observed_at,
                item.extraction_method,
                item.resolution_id,
                item.resolution_confidence,
                item.relevance,
                item.expires_at,
                item.active,
            ]
            for item in items
        ]
        with self._database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO candidate_evidence VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (symbol, source_type, source_record_id) DO UPDATE SET
                    evidence_id=excluded.evidence_id,
                    company_name=excluded.company_name,
                    source_excerpt=excluded.source_excerpt,
                    source_url=excluded.source_url,
                    event_at=excluded.event_at,
                    observed_at=excluded.observed_at,
                    extraction_method=excluded.extraction_method,
                    resolution_id=excluded.resolution_id,
                    resolution_confidence=excluded.resolution_confidence,
                    relevance=excluded.relevance,
                    expires_at=excluded.expires_at,
                    active=excluded.active
                """,
                parameters,
            )
        return len(items)

    def deactivate_evidence_not_seen(self, evidence_ids: set[str]) -> int:
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            before = self._database.fetchone(
                "SELECT COUNT(*) FROM candidate_evidence WHERE active=true"
            )
            self._database.execute(
                f"""
                UPDATE candidate_evidence SET active=false
                WHERE active=true AND evidence_id NOT IN ({placeholders})
                """,
                sorted(evidence_ids),
            )
        else:
            before = self._database.fetchone(
                "SELECT COUNT(*) FROM candidate_evidence WHERE active=true"
            )
            self._database.execute(
                "UPDATE candidate_evidence SET active=false WHERE active=true"
            )
        after = self._database.fetchone(
            "SELECT COUNT(*) FROM candidate_evidence WHERE active=true"
        )
        return int(before[0]) - int(after[0])

    def refresh_candidate_state(self, *, now: datetime) -> tuple[int, int]:
        self._database.execute(
            "UPDATE candidate_evidence SET active=false WHERE expires_at <= ?",
            [now],
        )
        self._database.execute(
            """
            INSERT INTO candidates
            SELECT symbol, arg_max(company_name, observed_at), MIN(observed_at),
                   MAX(observed_at), MAX(expires_at),
                   MAX(resolution_confidence), true, ?
            FROM candidate_evidence
            WHERE active=true AND expires_at > ?
            GROUP BY symbol
            ON CONFLICT (symbol) DO UPDATE SET
                company_name=excluded.company_name,
                first_seen_at=LEAST(candidates.first_seen_at, excluded.first_seen_at),
                last_seen_at=excluded.last_seen_at,
                expires_at=excluded.expires_at,
                highest_confidence=excluded.highest_confidence,
                active=true,
                updated_at=excluded.updated_at
            """,
            [now, now],
        )
        self._database.execute(
            """
            UPDATE candidates SET active=false, updated_at=?
            WHERE NOT EXISTS (
                SELECT 1 FROM candidate_evidence evidence
                WHERE evidence.symbol=candidates.symbol
                  AND evidence.active=true AND evidence.expires_at > ?
            )
            """,
            [now, now],
        )
        active = self._database.fetchone(
            "SELECT COUNT(*) FROM candidates WHERE active=true"
        )
        evidence = self._database.fetchone(
            "SELECT COUNT(*) FROM candidate_evidence WHERE active=true AND expires_at > ?",
            [now],
        )
        return int(active[0]), int(evidence[0])

    def record_refresh_summary(
        self,
        *,
        run_id: str,
        sources_scanned: int,
        resolutions_recorded: int,
        candidates_active: int,
        evidence_active: int,
        manual_review_count: int,
        recorded_at: datetime,
    ) -> None:
        self._database.execute(
            "INSERT INTO candidate_refresh_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                sources_scanned,
                resolutions_recorded,
                candidates_active,
                evidence_active,
                manual_review_count,
                recorded_at,
            ],
        )

    def list_candidates(self, *, active_only: bool = True, limit: int = 500) -> list[Candidate]:
        where = (
            "WHERE candidate.active=true AND candidate.expires_at > CURRENT_TIMESTAMP"
            if active_only
            else ""
        )
        rows = self._database.fetchall(
            f"""
            SELECT candidate.symbol, candidate.company_name, candidate.first_seen_at,
                   candidate.last_seen_at, candidate.expires_at,
                   candidate.highest_confidence, candidate.active,
                   candidate.updated_at, COUNT(evidence.evidence_id)
            FROM candidates candidate
            LEFT JOIN candidate_evidence evidence
              ON evidence.symbol=candidate.symbol AND evidence.active=true
             AND evidence.expires_at > CURRENT_TIMESTAMP
            {where}
            GROUP BY ALL
            ORDER BY candidate.last_seen_at DESC, candidate.symbol
            LIMIT ?
            """,
            [limit],
        )
        fields = tuple(Candidate.model_fields)
        return [Candidate(**dict(zip(fields, row, strict=True))) for row in rows]

    def count_candidates(self, *, active_only: bool = True) -> int:
        where = (
            " WHERE active=true AND expires_at > CURRENT_TIMESTAMP"
            if active_only
            else ""
        )
        row = self._database.fetchone(f"SELECT COUNT(*) FROM candidates{where}")
        return 0 if row is None else int(row[0])

    def list_analysis_symbols(self, *, limit: int = 5) -> tuple[str, ...]:
        """Blend fresh research evidence with recent adjusted-price movement."""

        if limit < 1:
            raise ValueError("analysis candidate limit must be positive")
        rows = self._database.fetchall(
            """
            WITH selected_provider AS (
                SELECT symbol, provider_id
                FROM market_bars
                WHERE interval='1d' AND adjustment='adjusted'
                GROUP BY symbol, provider_id
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY MAX(bar_end) DESC, COUNT(*) DESC, provider_id
                ) = 1
            ), deduplicated_bars AS (
                SELECT bar.symbol, bar.session_date, bar.close
                FROM market_bars bar
                JOIN selected_provider selected
                  ON selected.symbol=bar.symbol
                 AND selected.provider_id=bar.provider_id
                WHERE bar.interval='1d' AND bar.adjustment='adjusted'
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY bar.symbol, bar.session_date
                    ORDER BY bar.retrieved_at DESC, bar.bar_end DESC
                ) = 1
            ), recent_bars AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY symbol ORDER BY session_date DESC
                ) AS recency_rank
                FROM deduplicated_bars
            ), momentum AS (
                SELECT symbol, COUNT(*) AS session_count,
                       ABS((ARG_MAX(close, session_date) /
                            NULLIF(ARG_MIN(close, session_date), 0)) - 1) * 100
                           AS absolute_return_pct,
                       ((MAX(close) / NULLIF(MIN(close), 0)) - 1) * 100
                           AS range_pct
                FROM recent_bars
                WHERE recency_rank <= 63
                GROUP BY symbol
            ), evidence_rank AS (
            SELECT candidate.symbol
                 , LEAST(SUM(
                       CASE
                           WHEN evidence.source_type = 'news'
                            AND evidence.event_at >= CURRENT_TIMESTAMP - INTERVAL '3 days'
                           THEN 8 ELSE 0
                       END
                   ), 24)
                   + COUNT(DISTINCT evidence.source_type) * 8
                   + AVG(evidence.relevance) * 12 AS evidence_score
            FROM candidates candidate
            JOIN candidate_evidence evidence
              ON evidence.symbol=candidate.symbol
             AND evidence.active=true
             AND evidence.expires_at > CURRENT_TIMESTAMP
             AND evidence.source_type IN ('civictracker', 'news', 'earnings')
            WHERE candidate.active=true
              AND candidate.expires_at > CURRENT_TIMESTAMP
              AND candidate.symbol <> 'SPY'
              AND (
                  candidate.company_name <> candidate.symbol
                  OR evidence.source_type = 'civictracker'
              )
            GROUP BY candidate.symbol
            )
            SELECT ranked.symbol
            FROM evidence_rank ranked
            LEFT JOIN momentum ON momentum.symbol=ranked.symbol
            ORDER BY
                ranked.evidence_score
                + CASE
                    WHEN momentum.session_count >= 20 THEN LEAST(
                        40,
                        momentum.absolute_return_pct * 1.5
                        + momentum.range_pct * 0.5
                    )
                    ELSE 0
                  END DESC,
                COALESCE(momentum.absolute_return_pct, 0) DESC,
                ranked.symbol
            LIMIT ?
            """,
            [limit],
        )
        return tuple(str(row[0]) for row in rows)

    def get_candidate(
        self, symbol: str, *, active_only: bool = True
    ) -> Candidate | None:
        conditions = ["candidate.symbol=?"]
        if active_only:
            conditions.extend(
                ["candidate.active=true", "candidate.expires_at > CURRENT_TIMESTAMP"]
            )
        row = self._database.fetchone(
            f"""
            SELECT candidate.symbol, candidate.company_name, candidate.first_seen_at,
                   candidate.last_seen_at, candidate.expires_at,
                   candidate.highest_confidence, candidate.active,
                   candidate.updated_at, COUNT(evidence.evidence_id)
            FROM candidates candidate
            LEFT JOIN candidate_evidence evidence
              ON evidence.symbol=candidate.symbol AND evidence.active=true
             AND evidence.expires_at > CURRENT_TIMESTAMP
            WHERE {' AND '.join(conditions)}
            GROUP BY ALL
            """,
            [symbol.upper()],
        )
        if row is None:
            return None
        return Candidate(**dict(zip(tuple(Candidate.model_fields), row, strict=True)))

    def list_resolutions(
        self, *, status: ResolutionStatus | None = None, limit: int = 200
    ) -> list[EntityResolution]:
        where = "WHERE status=?" if status is not None else ""
        parameters: list[object] = [status.value] if status is not None else []
        parameters.append(limit)
        rows = self._database.fetchall(
            f"""
            SELECT resolution_id, source_type, source_record_id, mention_text,
                   normalized_mention, source_excerpt, status, symbol,
                   company_name, confidence, extraction_method, alias_version,
                   provider_id, provider_request_id, alternatives_json, reason,
                   resolved_at
            FROM entity_resolutions {where}
            ORDER BY resolved_at DESC LIMIT ?
            """,
            parameters,
        )
        fields = tuple(EntityResolution.model_fields)
        return [
            EntityResolution(
                **{
                    **dict(zip(fields, row, strict=True)),
                    "alternatives": tuple(json.loads(row[14])),
                }
            )
            for row in rows
        ]

    def list_evidence(
        self,
        *,
        symbol: str | None = None,
        active_only: bool = True,
        limit: int = 500,
    ) -> list[CandidateEvidence]:
        conditions: list[str] = []
        parameters: list[object] = []
        if symbol is not None:
            conditions.append("symbol=?")
            parameters.append(symbol)
        if active_only:
            conditions.append("active=true")
            conditions.append("expires_at > CURRENT_TIMESTAMP")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)
        rows = self._database.fetchall(
            f"""
            SELECT evidence_id, symbol, company_name, source_type,
                   source_record_id, source_excerpt, source_url, event_at,
                   observed_at, extraction_method, resolution_id,
                   resolution_confidence, relevance, expires_at, active
            FROM candidate_evidence {where}
            ORDER BY observed_at DESC LIMIT ?
            """,
            parameters,
        )
        fields = tuple(CandidateEvidence.model_fields)
        return [
            CandidateEvidence(**dict(zip(fields, row, strict=True))) for row in rows
        ]

    def company_name_for_symbol(self, symbol: str) -> str | None:
        row = self._database.fetchone(
            """
            SELECT company_name FROM company_aliases
            WHERE symbol=? AND active=true
            ORDER BY verified_at DESC LIMIT 1
            """,
            [symbol],
        )
        return None if row is None else str(row[0])

    def discovery_sources(self) -> tuple[dict[str, object], ...]:
        rows: list[dict[str, object]] = []
        snapshot = self._database.fetchone(
            """
            SELECT snapshot_id, captured_at FROM universe_snapshots
            ORDER BY captured_at DESC LIMIT 1
            """
        )
        if snapshot is not None:
            for member in self._database.fetchall(
                """
                SELECT symbol, company_name, sector, sub_industry
                FROM universe_members WHERE snapshot_id=?
                """,
                [snapshot[0]],
            ):
                rows.append(
                    {
                        "source_type": CandidateSourceType.SP500,
                        "source_record_id": f"{snapshot[0]}:{member[0]}",
                        "symbol": member[0],
                        "company_name": member[1],
                        "text": f"{member[1]} — {member[2]} / {member[3]}",
                        "url": None,
                        "event_at": snapshot[1],
                        "observed_at": snapshot[1],
                    }
                )
        for row in self._database.fetchall(
            """
            SELECT platform || ':' || post_id, content, original_url,
                   published_at, last_seen_at
            FROM social_posts
            WHERE discovery_eligible=true
            """
        ):
            rows.append(
                {
                    "source_type": CandidateSourceType.CIVICTRACKER,
                    "source_record_id": row[0],
                    "symbol": None,
                    "company_name": None,
                    "text": row[1],
                    "url": row[2],
                    "event_at": row[3],
                    "observed_at": row[4],
                }
            )
        for row in self._database.fetchall(
            """
            SELECT provider_id || ':' || symbol || ':' || source_record_id,
                   symbol, headline, summary, url, published_at, retrieved_at
            FROM market_news
            WHERE published_at >= CURRENT_TIMESTAMP - INTERVAL '14 days'
            """
        ):
            rows.append(
                {
                    "source_type": CandidateSourceType.NEWS,
                    "source_record_id": row[0],
                    "symbol": row[1],
                    "company_name": row[1],
                    "text": f"{row[2]} — {row[3]}",
                    "url": row[4],
                    "event_at": row[5],
                    "observed_at": row[6],
                }
            )
        for row in self._database.fetchall(
            """
            SELECT provider_id || ':' || symbol || ':' || source_record_id,
                   symbol, fiscal_period, report_date, known_available_at,
                   retrieved_at, reported_eps, estimated_eps
            FROM market_earnings
            WHERE report_date BETWEEN CURRENT_DATE - INTERVAL '30 days'
                                  AND CURRENT_DATE + INTERVAL '90 days'
            """
        ):
            event_at = datetime.combine(row[3], datetime.min.time(), tzinfo=row[4].tzinfo)
            eps_details = []
            if row[6] is not None:
                eps_details.append(f"reported EPS {float(row[6]):g}")
            if row[7] is not None:
                eps_details.append(f"estimated EPS {float(row[7]):g}")
            suffix = f"; {', '.join(eps_details)}" if eps_details else ""
            rows.append(
                {
                    "source_type": CandidateSourceType.EARNINGS,
                    "source_record_id": row[0],
                    "symbol": row[1],
                    "company_name": row[1],
                    "text": (
                        f"{row[1]} earnings for {row[2]} on "
                        f"{row[3].isoformat()}{suffix}"
                    ),
                    "url": None,
                    "event_at": min(event_at, row[4]),
                    "observed_at": row[5],
                }
            )
        return tuple(rows)
