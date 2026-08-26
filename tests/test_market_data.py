from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from investing_bot.db import Database, JobRunRepository, MarketDataRepository, MarketWriteKind
from investing_bot.models import (
    BarInterval,
    BarsRequest,
    CanonicalMetadata,
    MarketBar,
    PriceAdjustment,
    ProviderCapability,
)
from investing_bot.providers import (
    CapabilitySelection,
    CredentialPresenceStore,
    ProviderConfiguration,
    ProviderManager,
    ProviderRegistry,
    ProviderTarget,
)
from investing_bot.providers.fixtures import RecordedFixtureProvider, build_fixture_manifest
from investing_bot.services import (
    MarketDataCollector,
    MarketDataValidationError,
    MarketDataValidator,
    parse_sp500_html,
)


START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 15, tzinfo=UTC)


def open_market_repository(tmp_path: Path) -> tuple[Database, MarketDataRepository]:
    database = Database(tmp_path / "investing_bot.duckdb")
    database.connect()
    assert database.migrate() == 11
    return database, MarketDataRepository(database, dataset_root=tmp_path / "market")


class CountingFixtureProvider(RecordedFixtureProvider):
    def __init__(self) -> None:
        super().__init__("fixture_recorded")
        self.bar_calls = 0
        self.return_empty_bars = False

    async def fetch_bars(self, request: BarsRequest):
        self.bar_calls += 1
        result = await super().fetch_bars(request)
        if self.return_empty_bars:
            return result.model_copy(update={"items": ()})
        return result


def market_manager(provider: CountingFixtureProvider) -> ProviderManager:
    registry = ProviderRegistry()
    registry.register(build_fixture_manifest("fixture_recorded"), lambda: provider)
    return ProviderManager(
        registry=registry,
        configuration=ProviderConfiguration(
            selections={
                ProviderCapability.DAILY_BARS: CapabilitySelection(
                    primary=ProviderTarget(provider_id="fixture_recorded")
                )
            }
        ),
        credentials=CredentialPresenceStore(),
    )


def discovery_manager(provider: CountingFixtureProvider) -> ProviderManager:
    registry = ProviderRegistry()
    registry.register(build_fixture_manifest("fixture_recorded"), lambda: provider)
    target = CapabilitySelection(
        primary=ProviderTarget(provider_id="fixture_recorded")
    )
    return ProviderManager(
        registry=registry,
        configuration=ProviderConfiguration(
            selections={
                ProviderCapability.NEWS: target,
                ProviderCapability.EARNINGS: target,
            }
        ),
        credentials=CredentialPresenceStore(),
    )


@pytest.mark.anyio
async def test_market_repository_publishes_parquet_and_tracks_revisions(
    tmp_path: Path,
) -> None:
    database, repository = open_market_repository(tmp_path)
    provider = RecordedFixtureProvider("fixture_recorded")
    result = await provider.fetch_bars(
        BarsRequest(
            symbols=("SPY",),
            start=START,
            end=END,
            interval=BarInterval.DAY_1,
            adjustment=PriceAdjustment.ADJUSTED,
        )
    )
    bar = result.items[0]

    first = repository.store_bars((bar,))
    duplicate = repository.store_bars((bar,))
    revised_bar = bar.model_copy(
        update={
            "close": bar.close + 0.1,
            "high": max(bar.high, bar.close + 0.1),
            "metadata": bar.metadata.model_copy(update={"raw_payload_hash": "f" * 64}),
        }
    )
    revised = repository.store_bars((revised_bar,))

    assert first[MarketWriteKind.NEW] == 1
    assert duplicate[MarketWriteKind.DUPLICATE] == 1
    assert revised[MarketWriteKind.REVISED] == 1
    datasets = repository.list_datasets()
    assert len(datasets) == 1
    parquet = tmp_path / datasets[0].dataset_path
    assert parquet.is_file()
    assert database.fetchone(
        f"SELECT COUNT(*) FROM read_parquet('{str(parquet).replace(chr(39), chr(39) * 2)}')"
    ) == (1,)
    assert database.fetchone("SELECT COUNT(*) FROM market_bar_revisions") == (2,)
    assert repository.status().bar_count == 1
    database.close()


def test_market_repository_summarizes_recent_adjusted_momentum(
    tmp_path: Path,
) -> None:
    database, repository = open_market_repository(tmp_path)
    repository.store_bars(
        tuple(
            _daily_bar(date(2026, 5, 1) + timedelta(days=index), close)
            for index, close in enumerate((100.0, 108.0, 96.0, 125.0))
        )
    )

    momentum = repository.momentum("move")

    assert momentum is not None
    assert momentum.symbol == "MOVE"
    assert momentum.session_count == 4
    assert momentum.return_pct == pytest.approx(25.0)
    assert momentum.range_pct == pytest.approx((125.0 / 96.0 - 1) * 100)
    assert momentum.start_session == date(2026, 5, 1)
    assert momentum.end_session == date(2026, 5, 4)
    database.close()


@pytest.mark.anyio
async def test_market_collector_is_incremental_and_quarantines_partial_results(
    tmp_path: Path,
) -> None:
    database, repository = open_market_repository(tmp_path)
    provider = CountingFixtureProvider()
    manager = market_manager(provider)
    collector = MarketDataCollector(
        provider_manager=manager,
        repository=repository,
        jobs=JobRunRepository(database),
        validator=MarketDataValidator(now=lambda: END),
        now=lambda: END,
    )

    first = await collector.collect_bars(
        symbols=("SPY",),
        start=START,
        end=END,
        interval=BarInterval.DAY_1,
        adjustment=PriceAdjustment.ADJUSTED,
    )
    second = await collector.collect_bars(
        symbols=("SPY",),
        start=START,
        end=END,
        interval=BarInterval.DAY_1,
        adjustment=PriceAdjustment.ADJUSTED,
    )

    assert first.stored_records == 1
    assert second.stored_records == 0
    assert provider.bar_calls == 1
    assert repository.status().bar_count == 1

    provider.return_empty_bars = True
    no_new_bar = await collector.collect_bars(
        symbols=("SPY",),
        start=START,
        end=END + timedelta(hours=1),
        interval=BarInterval.DAY_1,
        adjustment=PriceAdjustment.ADJUSTED,
    )
    assert no_new_bar.stored_records == 0
    assert no_new_bar.quarantined is False
    assert repository.status().quarantine_count == 0

    with pytest.raises(MarketDataValidationError, match="AAPL"):
        await collector.collect_bars(
            symbols=("SPY", "AAPL"),
            start=START,
            end=END + timedelta(days=1),
            interval=BarInterval.DAY_1,
            adjustment=PriceAdjustment.ADJUSTED,
        )
    assert repository.status().bar_count == 1
    assert repository.status().quarantine_count == 1
    database.close()


@pytest.mark.anyio
async def test_constituent_discovery_rotates_persistently_and_records_empty_scans(
    tmp_path: Path,
) -> None:
    database, repository = open_market_repository(tmp_path)
    repository.save_universe_snapshot(
        snapshot_id="snapshot-1",
        source_url="https://example.com/sp500",
        captured_at=END,
        raw_payload_hash="a" * 64,
        members=tuple(
            {
                "symbol": symbol,
                "company_name": company,
                "sector": "Sector",
                "sub_industry": "Industry",
            }
            for symbol, company in (
                ("SPY", "Benchmark ETF"),
                ("AAPL", "Apple Inc."),
                ("CVX", "Chevron Corporation"),
                ("MSFT", "Microsoft Corporation"),
            )
        ),
    )
    assert repository.next_discovery_symbols(limit=2, exclude=("SPY",)) == (
        "AAPL",
        "CVX",
    )
    repository.record_discovery_scan(
        symbol="AAPL",
        scanned_at=END - timedelta(hours=1),
        succeeded=True,
        news_records=0,
        earnings_records=0,
    )
    assert repository.next_discovery_symbols(limit=2, exclude=("SPY",)) == (
        "CVX",
        "MSFT",
    )

    collector = MarketDataCollector(
        provider_manager=discovery_manager(CountingFixtureProvider()),
        repository=repository,
        jobs=JobRunRepository(database),
        now=lambda: END,
    )
    summary = await collector.collect_discovery_events(
        symbols=("CVX", "MSFT"), news_limit_per_symbol=10
    )

    assert summary.requested_symbols == 2
    assert summary.successful_symbols == 2
    assert summary.failed_symbols == 0
    assert summary.news_records > 0
    assert summary.earnings_records > 0
    assert database.fetchone(
        "SELECT news_records, earnings_records FROM market_discovery_scans "
        "WHERE symbol='MSFT'"
    ) == (0, 0)
    assert repository.next_discovery_symbols(limit=2, exclude=("SPY",)) == (
        "AAPL",
        "CVX",
    )
    database.close()


def test_sp500_parser_requires_a_complete_unique_snapshot() -> None:
    rows = "".join(
        f"<tr><td>S{i}</td><td>Company {i}</td><td>Sector</td>"
        f"<td>Industry</td><td>City</td><td>January 1, 2020</td>"
        f"<td>{i:010d}</td><td>2000</td></tr>"
        for i in range(500)
    )
    html = (
        "<table id='constituents'><thead><tr>"
        "<th>Symbol</th><th>Security</th><th>GICS Sector</th>"
        "<th>GICS Sub-Industry</th><th>Headquarters Location</th>"
        "<th>Date added</th><th>CIK</th><th>Founded</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )
    captured = datetime(2026, 8, 20, 12, tzinfo=UTC)

    snapshot = parse_sp500_html(html, captured_at=captured)

    assert len(snapshot.members) == 500
    assert snapshot.members[0].symbol == "S0"
    assert snapshot.members[0].date_added.isoformat() == "2020-01-01"
    assert snapshot.captured_at == captured
    assert len(snapshot.raw_payload_hash) == 64


def _daily_bar(session: date, close: float) -> MarketBar:
    bar_start = datetime.combine(session, time(13, 30), UTC)
    bar_end = datetime.combine(session, time(20), UTC)
    identity = f"MOVE:{session}:{close}"
    return MarketBar(
        symbol="MOVE",
        interval=BarInterval.DAY_1,
        bar_start=bar_start,
        bar_end=bar_end,
        session_date=session,
        exchange_timezone="America/New_York",
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1_000_000,
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
