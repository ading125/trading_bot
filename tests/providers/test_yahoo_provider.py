from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from investing_bot.models import (
    BarInterval,
    BarsRequest,
    CorporateActionsRequest,
    EarningsRequest,
    NewsRequest,
    PriceAdjustment,
    QuotesRequest,
    SymbolLookupRequest,
)
from investing_bot.providers.yahoo import YahooFinanceProvider


NOW = datetime(2026, 8, 20, 22, 0, tzinfo=UTC)


class FakeTicker:
    def __init__(
        self, symbol: str, *, actions: pd.DataFrame, earnings: pd.DataFrame
    ) -> None:
        self.symbol = symbol
        self.actions = actions
        self.earnings = earnings

    def get_fast_info(self) -> dict[str, object]:
        return {"last_price": 500.25, "bid": 500.2, "ask": 500.3, "currency": "USD"}

    def get_actions(self, *, period: str) -> pd.DataFrame:
        assert period == "max"
        return self.actions

    def get_news(self, *, count: int) -> list[dict[str, object]]:
        assert count == 5
        return [
            {
                "id": "news-1",
                "content": {
                    "title": "A verified headline",
                    "summary": "Summary",
                    "provider": {"displayName": "Example Wire"},
                    "canonicalUrl": {"url": "https://example.com/news-1"},
                    "pubDate": "2026-08-19T14:00:00Z",
                },
            }
        ]

    def get_earnings_dates(self, *, limit: int) -> pd.DataFrame:
        assert limit == 24
        return self.earnings


class FakeGateway:
    def __init__(self) -> None:
        self.download_kwargs: dict[str, object] = {}
        self.ticker_symbols: list[str] = []
        columns = pd.MultiIndex.from_product(
            [["BRK-B", "SPY"], ["Open", "High", "Low", "Close", "Volume"]]
        )
        self.bars = pd.DataFrame(
            [[500.0, 505.0, 499.0, 504.0, 1000, 650.0, 655.0, 649.0, 654.0, 2000]],
            index=pd.DatetimeIndex(["2026-08-19"]),
            columns=columns,
        )
        self.actions = pd.DataFrame(
            {"Dividends": [1.5], "Stock Splits": [0.0]},
            index=pd.DatetimeIndex(["2026-08-14"]),
        )
        self.earnings = pd.DataFrame(
            {"EPS Estimate": [2.1], "Reported EPS": [None]},
            index=pd.DatetimeIndex(["2026-08-25T16:00:00-04:00"]),
        )

    def download(self, **kwargs: object) -> pd.DataFrame:
        self.download_kwargs = kwargs
        return self.bars

    def ticker(self, symbol: str) -> FakeTicker:
        self.ticker_symbols.append(symbol)
        return FakeTicker(symbol, actions=self.actions, earnings=self.earnings)

    def search(self, query: str, **kwargs: object) -> SimpleNamespace:
        assert query == "Berkshire"
        return SimpleNamespace(
            quotes=[
                {
                    "symbol": "BRK-B",
                    "quoteType": "EQUITY",
                    "exchange": "NYQ",
                    "longname": "Berkshire Hathaway Inc.",
                }
            ]
        )


@pytest.mark.anyio
async def test_yahoo_adapter_normalizes_all_market_payloads_and_caches_raw(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway()
    provider = YahooFinanceProvider(
        gateway=gateway,
        raw_cache_dir=tmp_path,
        retries=0,
        now=lambda: NOW,
    )
    bars = await provider.fetch_bars(
        BarsRequest(
            symbols=("BRK.B", "SPY"),
            start=datetime(2026, 8, 18, tzinfo=UTC),
            end=NOW,
            interval=BarInterval.DAY_1,
            adjustment=PriceAdjustment.ADJUSTED,
        )
    )
    quotes = await provider.fetch_quotes(QuotesRequest(symbols=("BRK.B",)))
    actions = await provider.fetch_corporate_actions(
        CorporateActionsRequest(
            symbols=("BRK.B",),
            start=datetime(2026, 8, 1, tzinfo=UTC),
            end=NOW,
        )
    )
    news = await provider.fetch_news(
        NewsRequest(symbols=("BRK.B",), limit_per_symbol=5)
    )
    earnings = await provider.fetch_earnings(EarningsRequest(symbols=("BRK.B",)))
    lookup = await provider.lookup_symbols(SymbolLookupRequest(query="Berkshire"))

    assert gateway.download_kwargs["tickers"] == ["BRK-B", "SPY"]
    assert [item.symbol for item in bars.items] == ["BRK.B", "SPY"]
    assert bars.items[0].bar_start.hour == 13  # 09:30 America/New_York in UTC
    assert bars.items[0].bar_start.minute == 30
    assert bars.items[0].bar_end.hour == 20
    assert quotes.items[0].bid == 500.2
    assert actions.items[0].cash_amount == 1.5
    assert news.items[0].publisher == "Example Wire"
    assert earnings.items[0].report_date.isoformat() == "2026-08-25"
    assert earnings.items[0].metadata.event_at == NOW
    assert lookup.items[0].symbol == "BRK.B"
    assert gateway.ticker_symbols == ["BRK-B"] * 4
    assert len(list(tmp_path.rglob("*.json"))) == 6


@pytest.mark.anyio
async def test_yahoo_adapter_rejects_a_quote_without_a_price() -> None:
    gateway = FakeGateway()

    class MissingPriceTicker(FakeTicker):
        def get_fast_info(self) -> dict[str, object]:
            return {"currency": "USD"}

    gateway.ticker = lambda symbol: MissingPriceTicker(  # type: ignore[method-assign]
        symbol, actions=gateway.actions, earnings=gateway.earnings
    )
    provider = YahooFinanceProvider(gateway=gateway, retries=0, now=lambda: NOW)

    with pytest.raises(Exception, match="failed canonical validation"):
        await provider.fetch_quotes(QuotesRequest(symbols=("SPY",)))
