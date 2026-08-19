from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from investing_bot.config import AppSettings


def test_settings_build_database_path_below_absolute_data_dir(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path, environment="test")

    assert settings.database_path == tmp_path / "investing_bot.duckdb"
    assert str(settings.bind_host) == "127.0.0.1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data_dir", Path("relative/data")),
        ("database_name", "../outside.duckdb"),
        ("database_name", "application.sqlite"),
        ("provider_config_name", "../providers.json"),
        ("provider_config_name", "providers.toml"),
        ("port", 0),
    ],
)
def test_settings_reject_unsafe_runtime_paths_and_ports(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**{field: value})
