"""Validated, non-secret application configuration."""

from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Settings that are safe to expose in process metadata.

    Provider credentials intentionally do not belong in this model. Later
    milestones will store them encrypted and reference them by opaque ID.
    """

    model_config = SettingsConfigDict(
        env_prefix="INVESTING_BOT_",
        case_sensitive=False,
        extra="forbid",
    )

    environment: Literal["development", "test", "production"] = "production"
    data_dir: Path = Path("/data")
    database_name: str = "investing_bot.duckdb"
    provider_config_name: str = "providers.json"
    bind_host: IPv4Address | IPv6Address = IPv4Address("127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65_535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    civictracker_member_uuid: str = Field(
        default="3094abf7-4a95-4b8d-8c8d-af7d1c3747a1",
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    civictracker_poll_seconds: int = Field(default=900, ge=300, le=86_400)
    civictracker_page_size: int = Field(default=20, ge=1, le=100)
    civictracker_max_pages: int = Field(default=5, ge=1, le=100)
    civictracker_timeout_seconds: float = Field(default=15.0, ge=1, le=60)
    civictracker_retries: int = Field(default=2, ge=0, le=5)
    civictracker_collection_enabled: bool = True
    yahoo_repair_enabled: bool = False
    yahoo_timeout_seconds: float = Field(default=15.0, ge=1, le=60)
    yahoo_retries: int = Field(default=2, ge=0, le=5)
    market_collection_enabled: bool = False
    market_poll_seconds: int = Field(default=3_600, ge=900, le=86_400)
    market_seed_symbols: str = "SPY,AAPL,MSFT,CVX"
    market_daily_history_days: int = Field(default=730, ge=30, le=7_300)
    market_intraday_history_days: int = Field(default=5, ge=1, le=59)
    candidate_refresh_enabled: bool = True
    candidate_refresh_seconds: int = Field(default=900, ge=300, le=86_400)
    analysis_refresh_enabled: bool = True
    analysis_refresh_seconds: int = Field(default=3_600, ge=900, le=86_400)
    analysis_seed_symbols: str = "CVX"

    @field_validator("data_dir")
    @classmethod
    def require_absolute_data_dir(cls, value: Path) -> Path:
        """Keep runtime state in one explicit, absolute location."""

        if not value.is_absolute():
            raise ValueError("data_dir must be an absolute path")
        return value

    @field_validator("database_name")
    @classmethod
    def require_plain_database_filename(cls, value: str) -> str:
        """Prevent database paths from escaping the configured data directory."""

        if not value or value != Path(value).name or Path(value).suffix != ".duckdb":
            raise ValueError("database_name must be a plain .duckdb filename")
        return value

    @field_validator("provider_config_name")
    @classmethod
    def require_plain_provider_config_filename(cls, value: str) -> str:
        if not value or value != Path(value).name or Path(value).suffix != ".json":
            raise ValueError("provider_config_name must be a plain .json filename")
        return value

    @property
    def database_path(self) -> Path:
        """Return the database file below the persistent data directory."""

        return self.data_dir / self.database_name

    @property
    def provider_config_path(self) -> Path:
        return self.data_dir / self.provider_config_name

    @property
    def parsed_analysis_seed_symbols(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                symbol.strip().upper()
                for symbol in self.analysis_seed_symbols.split(",")
                if symbol.strip()
            )
        )

    @property
    def market_raw_cache_path(self) -> Path:
        return self.data_dir / "cache" / "yahoo"

    @property
    def market_dataset_path(self) -> Path:
        return self.data_dir / "market"

    @property
    def parsed_market_seed_symbols(self) -> tuple[str, ...]:
        symbols = tuple(
            symbol.strip().upper() for symbol in self.market_seed_symbols.split(",")
            if symbol.strip()
        )
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("market_seed_symbols must contain unique comma-separated symbols")
        return symbols


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Load and cache settings once for the process."""

    return AppSettings()
