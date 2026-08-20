"""Provider-neutral adapter over the pinned yfinance package."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from functools import partial
from hashlib import sha256
import json
from pathlib import Path
import random
from time import perf_counter
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import ValidationError
import yfinance as yf

from investing_bot.models import (
    BarInterval,
    BarsRequest,
    CanonicalMetadata,
    CorporateAction,
    CorporateActionsRequest,
    CorporateActionType,
    EarningsEvent,
    EarningsRequest,
    MarketBar,
    MarketQuote,
    NewsItem,
    NewsRequest,
    ProviderCapability,
    ProviderProvenance,
    ProviderResult,
    QuotesRequest,
    SymbolLookupRequest,
    SymbolMatch,
)
from investing_bot.providers.contracts import (
    ConnectionTestResult,
    CredentialReference,
    ProviderCallError,
    ProviderErrorCode,
    ProviderFailure,
    ProviderHealthState,
    ProviderManifest,
    RateLimitPolicy,
)


PROVIDER_ID = "yahoo_finance"
ADAPTER_VERSION = "0.1.0"
_EASTERN = ZoneInfo("America/New_York")
_SCHEMAS = {
    ProviderCapability.DAILY_BARS: "market_bar.v1",
    ProviderCapability.INTRADAY_BARS: "market_bar.v1",
    ProviderCapability.QUOTES: "market_quote.v1",
    ProviderCapability.CORPORATE_ACTIONS: "corporate_action.v1",
    ProviderCapability.SYMBOL_LOOKUP: "symbol_match.v1",
    ProviderCapability.NEWS: "news_item.v1",
    ProviderCapability.EARNINGS: "earnings_event.v1",
}
_US_EXCHANGES = frozenset({"ASE", "BTS", "NCM", "NGM", "NMS", "NYQ", "PCX"})


class YFinanceGateway:
    """Tiny injectable seam around provider-specific SDK objects."""

    def download(self, **kwargs: Any) -> pd.DataFrame | None:
        return yf.download(**kwargs)

    def ticker(self, symbol: str) -> Any:
        return yf.Ticker(symbol)

    def search(self, query: str, **kwargs: Any) -> Any:
        return yf.Search(query, **kwargs)


class YahooFinanceProvider:
    def __init__(
        self,
        *,
        gateway: YFinanceGateway | None = None,
        raw_cache_dir: Path | None = None,
        repair: bool = False,
        timeout_seconds: float = 15.0,
        retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.gateway = gateway or YFinanceGateway()
        self.raw_cache_dir = raw_cache_dir
        self.repair = repair
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self._sleep = sleep
        self._jitter = jitter
        self._now = now
        # An explicit small executor prevents the SDK from blocking FastAPI's
        # event loop and keeps concurrent Yahoo calls bounded.
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="investing-bot-yahoo"
        )
        self._manifest = build_yahoo_manifest()
        self._probe: (
            tuple[
                datetime,
                ProviderHealthState,
                ProviderErrorCode | None,
                str | None,
                float,
            ]
            | None
        ) = None

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    @property
    def lineage(self) -> str:
        return f"Yahoo Finance via yfinance 1.6.0;repair={str(self.repair).lower()}"

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def fetch_bars(self, request: BarsRequest) -> ProviderResult[MarketBar]:
        capability = (
            ProviderCapability.DAILY_BARS
            if request.interval is BarInterval.DAY_1
            else ProviderCapability.INTRADAY_BARS
        )
        interval = "1d" if request.interval is BarInterval.DAY_1 else "15m"
        auto_adjust = request.adjustment.value == "adjusted"
        frame = await self._call(
            capability,
            self.gateway.download,
            tickers=[_to_yahoo_symbol(symbol) for symbol in request.symbols],
            start=request.start,
            end=request.end,
            interval=interval,
            auto_adjust=auto_adjust,
            repair=self.repair,
            actions=False,
            group_by="ticker",
            threads=min(8, len(request.symbols)),
            progress=False,
            timeout=self.timeout_seconds,
            multi_level_index=True,
        )
        if frame is None or not isinstance(frame, pd.DataFrame):
            self._schema_failure(capability, "Yahoo returned no tabular bar response")
        self._cache_frame(capability, _request_key(request), frame)
        retrieved_at = self._aware_now()
        try:
            records = tuple(
                record
                for symbol in request.symbols
                for record in _normalize_bars(
                    frame,
                    symbol=symbol,
                    request=request,
                    provider_id=PROVIDER_ID,
                    adapter_version=ADAPTER_VERSION,
                    lineage=self.lineage,
                    retrieved_at=retrieved_at,
                )
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise self._schema_error(
                capability, "Yahoo bar data failed canonical validation"
            ) from exc
        return self._result(capability, records)

    async def fetch_quotes(
        self, request: QuotesRequest
    ) -> ProviderResult[MarketQuote]:
        capability = ProviderCapability.QUOTES
        retrieved_at = self._aware_now()
        records: list[MarketQuote] = []
        raw: dict[str, Any] = {}
        for symbol in request.symbols:
            ticker = self.gateway.ticker(_to_yahoo_symbol(symbol))
            info = await self._call(capability, ticker.get_fast_info)
            values = dict(info or {})
            raw[symbol] = values
            price = _first(values, "last_price", "lastPrice", "regularMarketPrice")
            currency = str(_first(values, "currency") or "USD").upper()
            try:
                records.append(
                    MarketQuote(
                        symbol=symbol,
                        as_of=retrieved_at,
                        price=float(price),
                        bid=_number(_first(values, "bid")),
                        ask=_number(_first(values, "ask")),
                        currency=currency,
                        metadata=self._metadata(
                            capability,
                            record_id=f"{symbol}:{retrieved_at.isoformat()}",
                            event_at=retrieved_at,
                            retrieved_at=retrieved_at,
                            raw=values,
                        ),
                    )
                )
            except (TypeError, ValueError, ValidationError) as exc:
                raise self._schema_error(
                    capability, f"Yahoo quote data for {symbol} failed canonical validation"
                ) from exc
        self._cache_json(capability, _request_key(request), raw)
        return self._result(capability, tuple(records))

    async def fetch_corporate_actions(
        self, request: CorporateActionsRequest
    ) -> ProviderResult[CorporateAction]:
        capability = ProviderCapability.CORPORATE_ACTIONS
        retrieved_at = self._aware_now()
        records: list[CorporateAction] = []
        raw_cache: dict[str, Any] = {}
        for symbol in request.symbols:
            data = await self._call(
                capability,
                self.gateway.ticker(_to_yahoo_symbol(symbol)).get_actions,
                period="max",
            )
            frame = _as_frame(data)
            raw_cache[symbol] = _frame_payload(frame)
            for index, row in frame.iterrows():
                event_at = _index_datetime(index, daily=True)
                if not request.start <= event_at <= request.end:
                    continue
                raw = _row_payload(index, row)
                dividend = _number(row.get("Dividends"))
                split = _number(row.get("Stock Splits"))
                if dividend and dividend > 0:
                    records.append(
                        CorporateAction(
                            symbol=symbol,
                            action_type=CorporateActionType.DIVIDEND,
                            effective_at=event_at,
                            cash_amount=dividend,
                            currency="USD",
                            metadata=self._metadata(
                                capability,
                                record_id=f"{symbol}:dividend:{event_at.isoformat()}",
                                event_at=event_at,
                                retrieved_at=retrieved_at,
                                raw=raw,
                            ),
                        )
                    )
                if split and split > 0:
                    records.append(
                        CorporateAction(
                            symbol=symbol,
                            action_type=CorporateActionType.SPLIT,
                            effective_at=event_at,
                            split_ratio=split,
                            metadata=self._metadata(
                                capability,
                                record_id=f"{symbol}:split:{event_at.isoformat()}",
                                event_at=event_at,
                                retrieved_at=retrieved_at,
                                raw=raw,
                            ),
                        )
                    )
        self._cache_json(capability, _request_key(request), raw_cache)
        return self._result(capability, tuple(records))

    async def lookup_symbols(
        self, request: SymbolLookupRequest
    ) -> ProviderResult[SymbolMatch]:
        capability = ProviderCapability.SYMBOL_LOOKUP
        search = await self._call(
            capability,
            self.gateway.search,
            request.query,
            max_results=10,
            news_count=0,
            lists_count=0,
            timeout=self.timeout_seconds,
            raise_errors=True,
        )
        quotes = list(getattr(search, "quotes", []) or [])
        self._cache_json(capability, _request_key(request), quotes)
        timestamp = self._aware_now()
        records: list[SymbolMatch] = []
        for position, raw in enumerate(quotes):
            symbol = str(raw.get("symbol", "")).upper().replace("-", ".")
            quote_type = str(raw.get("quoteType", raw.get("typeDisp", ""))).upper()
            exchange = str(raw.get("exchange", raw.get("exchangeDisp", ""))).upper()
            if not symbol or quote_type != "EQUITY":
                continue
            if request.us_listed_only and exchange not in _US_EXCHANGES:
                continue
            company = str(raw.get("longname") or raw.get("shortname") or symbol)
            exact = request.query.casefold() in {symbol.casefold(), company.casefold()}
            confidence = 1.0 if exact else max(0.5, 0.9 - position * 0.05)
            records.append(
                SymbolMatch(
                    symbol=symbol,
                    company_name=company,
                    exchange=exchange,
                    quote_type=quote_type.lower(),
                    active=True,
                    confidence=confidence,
                    metadata=self._metadata(
                        capability,
                        record_id=symbol,
                        event_at=timestamp,
                        retrieved_at=timestamp,
                        raw=raw,
                    ),
                )
            )
        return self._result(capability, tuple(records))

    async def fetch_news(self, request: NewsRequest) -> ProviderResult[NewsItem]:
        capability = ProviderCapability.NEWS
        retrieved_at = self._aware_now()
        records: list[NewsItem] = []
        raw_cache: dict[str, Any] = {}
        for symbol in request.symbols:
            raw_news = await self._call(
                capability,
                self.gateway.ticker(_to_yahoo_symbol(symbol)).get_news,
                count=request.limit_per_symbol,
            )
            items = list(raw_news or [])
            raw_cache[symbol] = items
            for raw in items:
                normalized = _news_fields(raw)
                if normalized is None:
                    continue
                published_at, headline, summary, publisher, url = normalized
                if request.published_after and published_at < request.published_after:
                    continue
                records.append(
                    NewsItem(
                        symbol=symbol,
                        headline=headline,
                        summary=summary,
                        publisher=publisher,
                        url=url,
                        published_at=published_at,
                        metadata=self._metadata(
                            capability,
                            record_id=str(raw.get("id") or url),
                            event_at=published_at,
                            retrieved_at=retrieved_at,
                            raw=raw,
                        ),
                    )
                )
        self._cache_json(capability, _request_key(request), raw_cache)
        return self._result(capability, tuple(records))

    async def fetch_earnings(
        self, request: EarningsRequest
    ) -> ProviderResult[EarningsEvent]:
        capability = ProviderCapability.EARNINGS
        retrieved_at = self._aware_now()
        records: list[EarningsEvent] = []
        raw_cache: dict[str, Any] = {}
        for symbol in request.symbols:
            data = await self._call(
                capability,
                self.gateway.ticker(_to_yahoo_symbol(symbol)).get_earnings_dates,
                limit=24,
            )
            frame = _as_frame(data)
            raw_cache[symbol] = _frame_payload(frame)
            for index, row in frame.iterrows():
                event_at = _index_datetime(index, daily=False)
                # A future scheduled date is not an event that has already happened.
                # Record when the schedule was observed while retaining report_date.
                metadata_event_at = min(event_at, retrieved_at)
                known_at = retrieved_at
                if request.known_after and known_at < request.known_after:
                    continue
                report_date = event_at.astimezone(_EASTERN).date()
                raw = _row_payload(index, row)
                records.append(
                    EarningsEvent(
                        symbol=symbol,
                        fiscal_period=f"{report_date.year}-Q{((report_date.month - 1) // 3) + 1}",
                        report_date=report_date,
                        reported_eps=_number(row.get("Reported EPS")),
                        estimated_eps=_number(row.get("EPS Estimate")),
                        revenue=None,
                        metadata=self._metadata(
                            capability,
                            record_id=f"{symbol}:{event_at.isoformat()}",
                            event_at=metadata_event_at,
                            retrieved_at=retrieved_at,
                            raw=raw,
                            known_available_at=known_at,
                        ),
                    )
                )
        self._cache_json(capability, _request_key(request), raw_cache)
        return self._result(capability, tuple(records))

    async def test_connection(
        self,
        capability: ProviderCapability,
        credential_ref: CredentialReference | None,
    ) -> ConnectionTestResult:
        checked_at = self._aware_now()
        if capability not in self.manifest.capabilities:
            return ConnectionTestResult(
                provider_id=PROVIDER_ID,
                capability=capability,
                state=ProviderHealthState.UNSUPPORTED,
                checked_at=checked_at,
                latency_ms=0,
                error_code=ProviderErrorCode.UNSUPPORTED,
                message="capability is not supported",
            )
        if self._probe is None or checked_at - self._probe[0] > timedelta(minutes=5):
            started = perf_counter()
            try:
                frame = await self._call(
                    ProviderCapability.DAILY_BARS,
                    self.gateway.download,
                    tickers=["SPY"],
                    period="5d",
                    interval="1d",
                    auto_adjust=True,
                    repair=False,
                    progress=False,
                    timeout=self.timeout_seconds,
                )
                if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
                    raise ValueError("Yahoo probe returned no SPY bars")
                state, code, message = ProviderHealthState.HEALTHY, None, None
            except ProviderCallError as exc:
                state, code, message = (
                    ProviderHealthState.UNHEALTHY,
                    exc.failure.code,
                    exc.failure.safe_message,
                )
            except (TypeError, ValueError):
                state, code, message = (
                    ProviderHealthState.UNHEALTHY,
                    ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                    "Yahoo probe returned an incompatible response",
                )
            self._probe = (
                checked_at,
                state,
                code,
                message,
                (perf_counter() - started) * 1_000,
            )
        _, state, code, message, latency = self._probe
        return ConnectionTestResult(
            provider_id=PROVIDER_ID,
            capability=capability,
            state=state,
            checked_at=checked_at,
            latency_ms=latency,
            error_code=code,
            message=message,
        )

    async def _call(
        self,
        capability: ProviderCapability,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        for attempt in range(self.retries + 1):
            try:
                future = self._executor.submit(partial(function, *args, **kwargs))
                deadline = asyncio.get_running_loop().time() + self.timeout_seconds + 5
                while not future.done():
                    if asyncio.get_running_loop().time() >= deadline:
                        future.cancel()
                        raise asyncio.TimeoutError
                    await asyncio.sleep(0.025)
                return future.result()
            except (asyncio.TimeoutError, OSError, RuntimeError, ConnectionError) as exc:
                if attempt < self.retries:
                    await self._sleep(0.5 * (2**attempt) + 0.1 * self._jitter())
                    continue
                raise ProviderCallError(
                    ProviderFailure(
                        provider_id=PROVIDER_ID,
                        capability=capability,
                        code=ProviderErrorCode.TRANSPORT,
                        safe_message=f"Yahoo Finance {capability.value} request failed",
                        retryable=True,
                    )
                ) from exc
        raise AssertionError("retry loop must return or raise")

    def _metadata(
        self,
        capability: ProviderCapability,
        *,
        record_id: str,
        event_at: datetime,
        retrieved_at: datetime,
        raw: Any,
        known_available_at: datetime | None = None,
    ) -> CanonicalMetadata:
        known = known_available_at or max(event_at, retrieved_at)
        return CanonicalMetadata(
            provider_id=PROVIDER_ID,
            provider_record_id=record_id,
            event_at=event_at,
            known_available_at=known,
            retrieved_at=max(retrieved_at, known),
            raw_payload_hash=_stable_hash(raw),
            schema_version=_SCHEMAS[capability],
            adapter_version=ADAPTER_VERSION,
            dataset_lineage=self.lineage,
        )

    def _result(
        self, capability: ProviderCapability, items: tuple[Any, ...]
    ) -> ProviderResult[Any]:
        return ProviderResult(
            items=items,
            provenance=ProviderProvenance(
                capability=capability,
                provider_id=PROVIDER_ID,
                adapter_version=ADAPTER_VERSION,
                schema_version=_SCHEMAS[capability],
                dataset_lineage=self.lineage,
                request_id=str(uuid4()),
            ),
        )

    def _schema_failure(self, capability: ProviderCapability, message: str) -> None:
        raise self._schema_error(capability, message)

    def _schema_error(self, capability: ProviderCapability, message: str) -> ProviderCallError:
        return ProviderCallError(
            ProviderFailure(
                provider_id=PROVIDER_ID,
                capability=capability,
                code=ProviderErrorCode.SCHEMA_INCOMPATIBLE,
                safe_message=message,
                retryable=False,
            )
        )

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("provider clock must be timezone-aware")
        return value.astimezone(UTC)

    def _cache_frame(self, capability: ProviderCapability, key: str, frame: pd.DataFrame) -> None:
        self._cache_text(capability, key, frame.to_json(orient="split", date_format="iso"))

    def _cache_json(self, capability: ProviderCapability, key: str, payload: Any) -> None:
        self._cache_text(capability, key, _stable_json(payload))

    def _cache_text(self, capability: ProviderCapability, key: str, payload: str) -> None:
        if self.raw_cache_dir is None:
            return
        directory = self.raw_cache_dir / capability.value
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = sha256(payload.encode("utf-8")).hexdigest()
        target = directory / f"{key}-{digest}.json"
        if target.exists():
            return
        temporary = target.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)


def build_yahoo_manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id=PROVIDER_ID,
        display_name="Yahoo Finance (yfinance)",
        adapter_version=ADAPTER_VERSION,
        capabilities=frozenset(_SCHEMAS),
        schema_versions=_SCHEMAS,
        rate_limit=RateLimitPolicy(requests=120, window_seconds=3_600),
        supported_intervals=frozenset({"1d", "15m"}),
        optional_features=frozenset(
            {"adjusted_bars", "repair", "batch_download", "raw_cache"}
        ),
        allowed_origins=("https://finance.yahoo.com", "https://query1.finance.yahoo.com"),
    )


def _normalize_bars(
    frame: pd.DataFrame,
    *,
    symbol: str,
    request: BarsRequest,
    provider_id: str,
    adapter_version: str,
    lineage: str,
    retrieved_at: datetime,
) -> tuple[MarketBar, ...]:
    symbol_frame = _symbol_frame(frame, symbol, len(request.symbols) == 1)
    records: list[MarketBar] = []
    for index, row in symbol_frame.iterrows():
        if row.isna().all():
            continue
        daily = request.interval is BarInterval.DAY_1
        source_time = _index_datetime(index, daily=daily)
        if daily:
            session_date = source_time.astimezone(_EASTERN).date()
            bar_start = datetime.combine(session_date, time(9, 30), tzinfo=_EASTERN).astimezone(UTC)
            bar_end = datetime.combine(session_date, time(16, 0), tzinfo=_EASTERN).astimezone(UTC)
        else:
            bar_start = source_time
            bar_end = bar_start + timedelta(minutes=15)
            session_date = bar_start.astimezone(_EASTERN).date()
        if bar_end > retrieved_at or not request.start <= bar_end <= request.end:
            continue
        raw = _row_payload(index, row)
        records.append(
            MarketBar(
                symbol=symbol,
                interval=request.interval,
                bar_start=bar_start,
                bar_end=bar_end,
                session_date=session_date,
                exchange_timezone="America/New_York",
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
                adjustment=request.adjustment,
                metadata=CanonicalMetadata(
                    provider_id=provider_id,
                    provider_record_id=f"{symbol}:{request.interval.value}:{bar_start.isoformat()}",
                    event_at=bar_end,
                    known_available_at=bar_end,
                    retrieved_at=max(retrieved_at, bar_end),
                    raw_payload_hash=_stable_hash(raw),
                    schema_version="market_bar.v1",
                    adapter_version=adapter_version,
                    dataset_lineage=lineage,
                ),
            )
        )
    return tuple(records)


def _symbol_frame(frame: pd.DataFrame, symbol: str, only_symbol: bool) -> pd.DataFrame:
    if not isinstance(frame.columns, pd.MultiIndex):
        if not only_symbol:
            raise ValueError("multi-symbol download did not return symbol columns")
        return frame
    levels = [
        set(str(value) for value in frame.columns.get_level_values(level))
        for level in range(frame.columns.nlevels)
    ]
    yahoo_symbol = _to_yahoo_symbol(symbol)
    for level, values in enumerate(levels):
        if yahoo_symbol in values:
            selected = frame.xs(yahoo_symbol, axis=1, level=level, drop_level=True)
            if isinstance(selected.columns, pd.MultiIndex):
                selected.columns = selected.columns.get_level_values(-1)
            return selected
    raise ValueError(f"download omitted symbol {symbol}")


def _to_yahoo_symbol(symbol: str) -> str:
    """Translate canonical share-class notation without leaking it downstream."""

    return symbol.replace(".", "-")


def _index_datetime(value: Any, *, daily: bool) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(_EASTERN)
    return timestamp.to_pydatetime().astimezone(UTC)


def _news_fields(raw: Mapping[str, Any]) -> tuple[datetime, str, str, str, str] | None:
    content = raw.get("content") if isinstance(raw.get("content"), Mapping) else raw
    assert isinstance(content, Mapping)
    headline = str(content.get("title") or "").strip()
    summary = str(content.get("summary") or content.get("description") or "").strip()
    provider = content.get("provider")
    publisher = (
        str(provider.get("displayName") or "Yahoo Finance")
        if isinstance(provider, Mapping)
        else str(content.get("publisher") or "Yahoo Finance")
    )
    url_value = content.get("canonicalUrl") or content.get("clickThroughUrl") or content.get("link")
    url = str(url_value.get("url") if isinstance(url_value, Mapping) else url_value or "")
    published = (
        content.get("pubDate")
        or content.get("providerPublishTime")
        or content.get("displayTime")
    )
    if not headline or not url.startswith("https://") or published is None:
        return None
    if isinstance(published, (int, float)):
        published_at = datetime.fromtimestamp(published, tz=UTC)
    else:
        published_at = datetime.fromisoformat(str(published).replace("Z", "+00:00")).astimezone(UTC)
    return published_at, headline, summary, publisher, url


def _as_frame(value: Any) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.Series):
        return value.to_frame()
    if not isinstance(value, pd.DataFrame):
        raise ValueError("provider response is not tabular")
    return value


def _number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _first(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value is not None and not pd.isna(value):
            return value
    return None


def _row_payload(index: Any, row: pd.Series) -> dict[str, Any]:
    return {"index": str(index), **{str(key): _json_value(value) for key, value in row.items()}}


def _frame_payload(frame: pd.DataFrame) -> Any:
    return json.loads(frame.to_json(orient="split", date_format="iso"))


def _json_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _stable_json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _stable_hash(value: Any) -> str:
    return sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _request_key(request: Any) -> str:
    return sha256(request.model_dump_json().encode("utf-8")).hexdigest()[:24]
