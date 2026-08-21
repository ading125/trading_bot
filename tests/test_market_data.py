from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from investing_bot.db import Database, JobRunRepository, MarketDataRepository, MarketWriteKind
from investing_bot.models import (
    BarInterval,
    BarsRequest,
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
    assert database.migrate() == 7
    return database, MarketDataRepository(database, dataset_root=tmp_path / "market")


class CountingFixtureProvider(RecordedFixtureProvider):
    def __init__(self) -> None:
        super().__init__("fixture_recorded")
        self.bar_calls = 0

    async def fetch_bars(self, request: BarsRequest):
        self.bar_calls += 1
        return await super().fetch_bars(request)


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
