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
    bind_host: IPv4Address | IPv6Address = IPv4Address("127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65_535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"

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

    @property
    def database_path(self) -> Path:
        """Return the database file below the persistent data directory."""

        return self.data_dir / self.database_name


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Load and cache settings once for the process."""

    return AppSettings()
