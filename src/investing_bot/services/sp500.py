"""Point-in-time S&P 500 universe snapshots."""

from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import uuid4

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession, RequestsError
from pydantic import BaseModel, ConfigDict, Field

from investing_bot.db import MarketDataRepository


SP500_SOURCE_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


class SP500Member(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.-]*$")
    company_name: str
    sector: str
    sub_industry: str
    headquarters: str | None = None
    date_added: date | None = None
    cik: str | None = None
    founded: str | None = None


class SP500Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    source_url: str
    captured_at: datetime
    raw_payload_hash: str
    members: tuple[SP500Member, ...]


def parse_sp500_html(
    html: str,
    *,
    captured_at: datetime,
    source_url: str = SP500_SOURCE_URL,
) -> SP500Snapshot:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table#constituents") or soup.select_one("table.wikitable")
    if table is None:
        raise ValueError("S&P 500 constituents table is missing")
    headers = [cell.get_text(" ", strip=True) for cell in table.select("thead th")]
    if not headers:
        first_row = table.select_one("tr")
        headers = (
            [cell.get_text(" ", strip=True) for cell in first_row.select("th")]
            if first_row
            else []
        )
    required = {"Symbol", "Security", "GICS Sector", "GICS Sub-Industry"}
    if not required.issubset(headers):
        raise ValueError("S&P 500 table headers are incompatible")
    members: list[SP500Member] = []
    rows = table.select("tbody tr") or table.select("tr")[1:]
    for row in rows:
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th, td")]
        if len(cells) < len(headers):
            continue
        raw = dict(zip(headers, cells, strict=False))
        if raw.get("Symbol") == "Symbol":
            continue
        date_text = raw.get("Date added", "")
        members.append(
            SP500Member(
                symbol=raw["Symbol"].replace("\n", "").strip().upper(),
                company_name=raw["Security"],
                sector=raw["GICS Sector"],
                sub_industry=raw["GICS Sub-Industry"],
                headquarters=raw.get("Headquarters Location"),
                date_added=_parse_date(date_text),
                cik=raw.get("CIK"),
                founded=raw.get("Founded"),
            )
        )
    if not 490 <= len(members) <= 520:
        raise ValueError("S&P 500 snapshot has an implausible member count")
    if len({member.symbol for member in members}) != len(members):
        raise ValueError("S&P 500 snapshot contains duplicate symbols")
    return SP500Snapshot(
        snapshot_id=str(uuid4()),
        source_url=source_url,
        captured_at=captured_at.astimezone(UTC),
        raw_payload_hash=sha256(html.encode("utf-8")).hexdigest(),
        members=tuple(members),
    )


def _parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for format_string in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, format_string).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported S&P 500 date format: {value}")


class SP500UniverseCollector:
    def __init__(
        self,
        repository: MarketDataRepository,
        *,
        timeout_seconds: float = 15.0,
        user_agent: str = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/128 Safari/537.36 InvestingBot/0.1"
        ),
    ) -> None:
        self.repository = repository
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    async def collect(self, *, client: object | None = None) -> SP500Snapshot:
        owned = client is None
        http = client or AsyncSession(
            impersonate="chrome",
            headers={"User-Agent": self.user_agent},
        )
        try:
            response = await http.get(SP500_SOURCE_URL, timeout=self.timeout_seconds)
            response.raise_for_status()
        except RequestsError as exc:
            raise RuntimeError("S&P 500 universe source could not be reached") from exc
        finally:
            if owned:
                await http.close()
        snapshot = parse_sp500_html(response.text, captured_at=datetime.now(UTC))
        self.repository.save_universe_snapshot(
            snapshot_id=snapshot.snapshot_id,
            source_url=snapshot.source_url,
            captured_at=snapshot.captured_at,
            raw_payload_hash=snapshot.raw_payload_hash,
            members=tuple(member.model_dump() for member in snapshot.members),
        )
        return snapshot
