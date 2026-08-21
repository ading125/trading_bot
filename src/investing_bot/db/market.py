"""DuckDB catalog and canonical Parquet publication for market data."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict

from investing_bot.db.database import Database
from investing_bot.models import (
    BarInterval,
    CorporateAction,
    EarningsEvent,
    MarketBar,
    MarketQuote,
    NewsItem,
    PriceAdjustment,
    StrategyBar,
)


class MarketWriteKind(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    REVISED = "revised"


class MarketDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_path: str
    provider_id: str
    symbol: str
    interval: str
    adjustment: str
    partition_year: int
    row_count: int
    first_bar_at: AwareDatetime
    last_bar_at: AwareDatetime
    file_hash: str
    repaired: bool
    published_at: AwareDatetime


class MarketStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bar_count: int
    symbol_count: int
    dataset_count: int
    quarantine_count: int
    latest_bar_at: AwareDatetime | None
    universe_members: int
    news_count: int
    earnings_count: int


class MarketDataRepository:
    def __init__(self, database: Database, *, dataset_root: Path) -> None:
        self._database = database
        self.dataset_root = dataset_root

    def latest_bar_end(
        self,
        *,
        provider_id: str,
        symbol: str,
        interval: BarInterval,
        adjustment: PriceAdjustment,
    ) -> datetime | None:
        row = self._database.fetchone(
            """
            SELECT MAX(bar_end) FROM market_bars
            WHERE provider_id = ? AND symbol = ? AND interval = ? AND adjustment = ?
            """,
            [provider_id, symbol, interval.value, adjustment.value],
        )
        return None if row is None or row[0] is None else row[0]

    def latest_covered_end(
        self,
        *,
        provider_id: str,
        symbol: str,
        interval: BarInterval,
        adjustment: PriceAdjustment,
    ) -> datetime | None:
        row = self._database.fetchone(
            """
            SELECT covered_through FROM market_collection_cursors
            WHERE provider_id=? AND symbol=? AND interval=? AND adjustment=?
            """,
            [provider_id, symbol, interval.value, adjustment.value],
        )
        return None if row is None else row[0]

    def mark_covered(
        self,
        *,
        provider_id: str,
        symbols: tuple[str, ...],
        interval: BarInterval,
        adjustment: PriceAdjustment,
        covered_through: datetime,
    ) -> None:
        for symbol in symbols:
            self._database.execute(
                """
                INSERT INTO market_collection_cursors VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET
                    covered_through=GREATEST(
                        market_collection_cursors.covered_through,
                        excluded.covered_through
                    ),
                    updated_at=excluded.updated_at
                """,
                [
                    provider_id,
                    symbol,
                    interval.value,
                    adjustment.value,
                    covered_through,
                    datetime.now(UTC),
                ],
            )

    def store_bars(self, bars: tuple[MarketBar, ...]) -> dict[MarketWriteKind, int]:
        counts = {kind: 0 for kind in MarketWriteKind}
        affected: set[tuple[str, str, str, str, int]] = set()
        with self._database.transaction() as connection:
            for bar in bars:
                key = [
                    bar.metadata.provider_id,
                    bar.symbol,
                    bar.interval.value,
                    bar.adjustment.value,
                    bar.bar_start,
                ]
                existing = connection.execute(
                    """
                    SELECT raw_payload_hash FROM market_bars
                    WHERE provider_id = ? AND symbol = ? AND interval = ?
                      AND adjustment = ? AND bar_start = ?
                    """,
                    key,
                ).fetchone()
                # Republishing duplicates makes an interrupted DB-then-file write
                # self-healing on the next collection run.
                affected.add(
                    (
                        bar.metadata.provider_id,
                        bar.symbol,
                        bar.interval.value,
                        bar.adjustment.value,
                        bar.session_date.year,
                    )
                )
                if existing is not None and str(existing[0]) == bar.metadata.raw_payload_hash:
                    counts[MarketWriteKind.DUPLICATE] += 1
                    continue
                repaired = "repair=true" in bar.metadata.dataset_lineage
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO market_bars VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        [
                            *key,
                            bar.bar_end,
                            bar.session_date,
                            bar.exchange_timezone,
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.volume,
                            bar.metadata.known_available_at,
                            bar.metadata.retrieved_at,
                            bar.metadata.raw_payload_hash,
                            bar.metadata.schema_version,
                            bar.metadata.adapter_version,
                            bar.metadata.dataset_lineage,
                            repaired,
                        ],
                    )
                    revision = 1
                    kind = MarketWriteKind.NEW
                else:
                    prior = connection.execute(
                        """
                        SELECT COALESCE(MAX(revision), 0) FROM market_bar_revisions
                        WHERE provider_id = ? AND symbol = ? AND interval = ?
                          AND adjustment = ? AND bar_start = ?
                        """,
                        key,
                    ).fetchone()
                    revision = int(prior[0]) + 1
                    connection.execute(
                        """
                        UPDATE market_bars SET
                            bar_end=?, session_date=?, exchange_timezone=?, open=?, high=?,
                            low=?, close=?, volume=?, known_available_at=?, retrieved_at=?,
                            raw_payload_hash=?, schema_version=?, adapter_version=?,
                            dataset_lineage=?, repaired=?
                        WHERE provider_id=? AND symbol=? AND interval=?
                          AND adjustment=? AND bar_start=?
                        """,
                        [
                            bar.bar_end,
                            bar.session_date,
                            bar.exchange_timezone,
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.volume,
                            bar.metadata.known_available_at,
                            bar.metadata.retrieved_at,
                            bar.metadata.raw_payload_hash,
                            bar.metadata.schema_version,
                            bar.metadata.adapter_version,
                            bar.metadata.dataset_lineage,
                            repaired,
                            *key,
                        ],
                    )
                    kind = MarketWriteKind.REVISED
                connection.execute(
                    """
                    INSERT INTO market_bar_revisions VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        *key,
                        revision,
                        bar.metadata.raw_payload_hash,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.metadata.retrieved_at,
                    ],
                )
                counts[kind] += 1
        for partition in affected:
            self._publish_partition(*partition)
        return counts

    def quarantine(
        self,
        *,
        run_id: str,
        provider_id: str,
        capability: str,
        request_hash: str,
        reason: str,
        payload: object,
    ) -> str:
        payload_json = json.dumps(
            payload, default=str, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        quarantine_id = str(uuid4())
        self._database.execute(
            "INSERT INTO market_quarantine VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                quarantine_id,
                run_id,
                provider_id,
                capability,
                request_hash,
                " ".join(reason.split())[:500],
                sha256(payload_json.encode()).hexdigest(),
                payload_json,
                datetime.now(UTC),
            ],
        )
        return quarantine_id

    def store_actions(self, items: tuple[CorporateAction, ...]) -> int:
        for item in items:
            self._database.execute(
                """
                INSERT INTO market_corporate_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET
                    cash_amount=excluded.cash_amount,
                    split_ratio=excluded.split_ratio,
                    currency=excluded.currency,
                    raw_payload_hash=excluded.raw_payload_hash,
                    retrieved_at=excluded.retrieved_at
                """,
                [
                    item.metadata.provider_id,
                    item.symbol,
                    item.action_type.value,
                    item.effective_at,
                    item.cash_amount,
                    item.split_ratio,
                    item.currency,
                    item.metadata.raw_payload_hash,
                    item.metadata.retrieved_at,
                ],
            )
        return len(items)

    def store_quotes(self, items: tuple[MarketQuote, ...]) -> int:
        for item in items:
            self._database.execute(
                """
                INSERT INTO market_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET
                    price=excluded.price, bid=excluded.bid, ask=excluded.ask,
                    currency=excluded.currency,
                    raw_payload_hash=excluded.raw_payload_hash,
                    retrieved_at=excluded.retrieved_at
                """,
                [
                    item.metadata.provider_id,
                    item.symbol,
                    item.as_of,
                    item.price,
                    item.bid,
                    item.ask,
                    item.currency,
                    item.metadata.raw_payload_hash,
                    item.metadata.retrieved_at,
                ],
            )
        return len(items)

    def record_collection_summary(self, summary: object) -> None:
        values = summary.model_dump(mode="python")
        capability = values["capability"]
        self._database.execute(
            "INSERT INTO market_collection_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                values["run_id"],
                values["provider_id"],
                capability.value if hasattr(capability, "value") else capability,
                values["requested_symbols"],
                values["stored_records"],
                values["duplicate_records"],
                values["revised_records"],
                values["quarantined"],
                values["recorded_at"],
            ],
        )

    def store_news(self, items: tuple[NewsItem, ...]) -> int:
        for item in items:
            self._database.execute(
                """
                INSERT INTO market_news VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET
                    headline=excluded.headline, summary=excluded.summary,
                    publisher=excluded.publisher, url=excluded.url,
                    known_available_at=excluded.known_available_at,
                    retrieved_at=excluded.retrieved_at,
                    raw_payload_hash=excluded.raw_payload_hash
                """,
                [
                    item.metadata.provider_id,
                    item.symbol,
                    item.metadata.provider_record_id or item.url,
                    item.headline,
                    item.summary,
                    item.publisher,
                    item.url,
                    item.published_at,
                    item.metadata.known_available_at,
                    item.metadata.retrieved_at,
                    item.metadata.raw_payload_hash,
                ],
            )
        return len(items)

    def store_earnings(self, items: tuple[EarningsEvent, ...]) -> int:
        for item in items:
            self._database.execute(
                """
                INSERT INTO market_earnings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET
                    fiscal_period=excluded.fiscal_period,
                    report_date=excluded.report_date,
                    reported_eps=excluded.reported_eps,
                    estimated_eps=excluded.estimated_eps,
                    revenue=excluded.revenue,
                    known_available_at=excluded.known_available_at,
                    retrieved_at=excluded.retrieved_at,
                    raw_payload_hash=excluded.raw_payload_hash
                """,
                [
                    item.metadata.provider_id,
                    item.symbol,
                    item.metadata.provider_record_id or item.fiscal_period,
                    item.fiscal_period,
                    item.report_date,
                    item.reported_eps,
                    item.estimated_eps,
                    item.revenue,
                    item.metadata.known_available_at,
                    item.metadata.retrieved_at,
                    item.metadata.raw_payload_hash,
                ],
            )
        return len(items)

    def save_universe_snapshot(
        self,
        *,
        snapshot_id: str,
        source_url: str,
        captured_at: datetime,
        raw_payload_hash: str,
        members: tuple[dict[str, object], ...],
    ) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO universe_snapshots VALUES (?, ?, ?, ?, ?)",
                [snapshot_id, source_url, captured_at, raw_payload_hash, len(members)],
            )
            for member in members:
                connection.execute(
                    "INSERT INTO universe_members VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        snapshot_id,
                        member["symbol"],
                        member["company_name"],
                        member["sector"],
                        member["sub_industry"],
                        member.get("headquarters"),
                        member.get("date_added"),
                        member.get("cik"),
                        member.get("founded"),
                    ],
                )

    def status(self) -> MarketStatus:
        row = self._database.fetchone(
            """
            SELECT
                (SELECT COUNT(*) FROM market_bars),
                (SELECT COUNT(DISTINCT symbol) FROM market_bars),
                (SELECT COUNT(*) FROM market_datasets),
                (SELECT COUNT(*) FROM market_quarantine),
                (SELECT MAX(bar_end) FROM market_bars),
                (SELECT COALESCE(MAX(member_count), 0) FROM universe_snapshots),
                (SELECT COUNT(*) FROM market_news),
                (SELECT COUNT(*) FROM market_earnings)
            """
        )
        assert row is not None
        return MarketStatus(
            bar_count=int(row[0]),
            symbol_count=int(row[1]),
            dataset_count=int(row[2]),
            quarantine_count=int(row[3]),
            latest_bar_at=row[4],
            universe_members=int(row[5]),
            news_count=int(row[6]),
            earnings_count=int(row[7]),
        )

    def list_bars(
        self,
        *,
        symbol: str,
        interval: BarInterval,
        adjustment: PriceAdjustment,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        rows = self._database.fetchall(
            """
            SELECT symbol, interval, adjustment, bar_start, bar_end, session_date,
                   open, high, low, close, volume, provider_id, repaired
            FROM market_bars
            WHERE symbol=? AND interval=? AND adjustment=?
            ORDER BY bar_start DESC LIMIT ?
            """,
            [symbol, interval.value, adjustment.value, limit],
        )
        fields = (
            "symbol", "interval", "adjustment", "bar_start", "bar_end",
            "session_date", "open", "high", "low", "close", "volume",
            "provider_id", "repaired",
        )
        return [dict(zip(fields, row, strict=True)) for row in rows]

    def resolve_strategy_provider(
        self,
        *,
        symbol: str,
        benchmark_symbol: str,
        as_of: datetime,
    ) -> str | None:
        symbols = tuple(dict.fromkeys((symbol.upper(), benchmark_symbol.upper())))
        placeholders = ",".join("?" for _ in symbols)
        row = self._database.fetchone(
            f"""
            SELECT provider_id
            FROM market_bars
            WHERE symbol IN ({placeholders})
              AND interval=? AND adjustment=?
              AND bar_end <= ? AND known_available_at <= ?
            GROUP BY provider_id
            HAVING COUNT(DISTINCT symbol) = ?
            ORDER BY MAX(bar_end) DESC, provider_id
            LIMIT 1
            """,
            [
                *symbols,
                BarInterval.DAY_1.value,
                PriceAdjustment.ADJUSTED.value,
                as_of,
                as_of,
                len(symbols),
            ],
        )
        return None if row is None else str(row[0])

    def strategy_bars(
        self,
        *,
        provider_id: str,
        symbol: str,
        interval: BarInterval,
        as_of: datetime,
        limit: int,
    ) -> tuple[StrategyBar, ...]:
        rows = self._database.fetchall(
            """
            SELECT symbol, interval, bar_start, bar_end, session_date,
                   open, high, low, close, volume, known_available_at,
                   provider_id, repaired
            FROM (
                SELECT symbol, interval, bar_start, bar_end, session_date,
                       open, high, low, close, volume, known_available_at,
                       provider_id, repaired
                FROM market_bars
                WHERE provider_id=? AND symbol=? AND interval=?
                  AND adjustment=? AND bar_end <= ? AND known_available_at <= ?
                ORDER BY bar_end DESC LIMIT ?
            ) selected
            ORDER BY bar_end
            """,
            [
                provider_id,
                symbol.upper(),
                interval.value,
                PriceAdjustment.ADJUSTED.value,
                as_of,
                as_of,
                limit,
            ],
        )
        fields = tuple(StrategyBar.model_fields)
        return tuple(
            StrategyBar(**dict(zip(fields, row, strict=True))) for row in rows
        )

    def research_bars(
        self,
        *,
        provider_id: str,
        symbol: str,
        start: date,
        end: date,
    ) -> tuple[StrategyBar, ...]:
        """Return one immutable adjusted daily history for a research run."""

        rows = self._database.fetchall(
            """
            SELECT symbol, interval, bar_start, bar_end, session_date,
                   open, high, low, close, volume, known_available_at,
                   provider_id, repaired
            FROM market_bars
            WHERE provider_id=? AND symbol=? AND interval=? AND adjustment=?
              AND session_date BETWEEN ? AND ?
            ORDER BY bar_end
            """,
            [
                provider_id,
                symbol.upper(),
                BarInterval.DAY_1.value,
                PriceAdjustment.ADJUSTED.value,
                start,
                end,
            ],
        )
        fields = tuple(StrategyBar.model_fields)
        return tuple(
            StrategyBar(**dict(zip(fields, row, strict=True))) for row in rows
        )

    def list_datasets(self) -> list[MarketDataset]:
        rows = self._database.fetchall(
            """
            SELECT dataset_path, provider_id, symbol, interval, adjustment,
                   partition_year, row_count, first_bar_at, last_bar_at,
                   file_hash, repaired, published_at
            FROM market_datasets ORDER BY symbol, interval, partition_year DESC
            """
        )
        fields = tuple(MarketDataset.model_fields)
        return [MarketDataset(**dict(zip(fields, row, strict=True))) for row in rows]

    def _publish_partition(
        self,
        provider_id: str,
        symbol: str,
        interval: str,
        adjustment: str,
        year: int,
    ) -> None:
        safe_parts = (provider_id, symbol, interval, adjustment)
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
        if any(
            not part or any(char not in allowed for char in part)
            for part in safe_parts
        ):
            raise ValueError("unsafe market partition identity")
        directory = (
            self.dataset_root
            / "bars"
            / f"interval={interval}"
            / f"adjustment={adjustment}"
            / f"symbol={symbol}"
            / f"year={year}"
        )
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / "bars.parquet"
        temporary = directory / f".{uuid4()}.tmp.parquet"
        escaped = str(temporary).replace("'", "''")
        self._database.execute(
            f"""
            COPY (
                SELECT symbol, interval, adjustment, bar_start, bar_end,
                       session_date, exchange_timezone, open, high, low, close,
                       volume, known_available_at, retrieved_at, provider_id,
                       raw_payload_hash, schema_version, adapter_version,
                       dataset_lineage, repaired
                FROM market_bars
                WHERE provider_id=? AND symbol=? AND interval=? AND adjustment=?
                  AND year(session_date)=?
                ORDER BY bar_start
            ) TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [provider_id, symbol, interval, adjustment, year],
        )
        temporary.replace(target)
        file_hash = sha256(target.read_bytes()).hexdigest()
        row = self._database.fetchone(
            """
            SELECT COUNT(*), MIN(bar_start), MAX(bar_end), BOOL_OR(repaired)
            FROM market_bars
            WHERE provider_id=? AND symbol=? AND interval=? AND adjustment=?
              AND year(session_date)=?
            """,
            [provider_id, symbol, interval, adjustment, year],
        )
        assert row is not None and int(row[0]) > 0
        self._database.execute(
            """
            INSERT INTO market_datasets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (dataset_path) DO UPDATE SET
                row_count=excluded.row_count,
                first_bar_at=excluded.first_bar_at,
                last_bar_at=excluded.last_bar_at,
                file_hash=excluded.file_hash,
                repaired=excluded.repaired,
                published_at=excluded.published_at
            """,
            [
                str(target.relative_to(self.dataset_root.parent)),
                provider_id,
                symbol,
                interval,
                adjustment,
                year,
                int(row[0]),
                row[1],
                row[2],
                file_hash,
                bool(row[3]),
                datetime.now(UTC),
            ],
        )
