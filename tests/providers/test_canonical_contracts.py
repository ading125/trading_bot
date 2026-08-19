from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from investing_bot.models import (
    BarInterval,
    CanonicalMetadata,
    MarketBar,
    PriceAdjustment,
    ProviderCapability,
)
from investing_bot.providers.contracts import (
    CapabilitySelection,
    ProviderConfiguration,
    ProviderManifest,
    ProviderTarget,
)


NOW = datetime(2026, 8, 14, 14, 0, tzinfo=UTC)
ROOT = Path(__file__).parents[2]


def metadata(**changes: object) -> CanonicalMetadata:
    values: dict[str, object] = {
        "provider_id": "fixture_recorded",
        "provider_record_id": "record-1",
        "event_at": NOW - timedelta(minutes=2),
        "known_available_at": NOW - timedelta(minutes=1),
        "retrieved_at": NOW,
        "raw_payload_hash": "a" * 64,
        "schema_version": "market_bar.v1",
        "adapter_version": "1.0.0",
        "dataset_lineage": "fixture:test:v1",
    }
    values.update(changes)
    return CanonicalMetadata.model_validate(values)


def test_point_in_time_metadata_rejects_impossible_availability_order() -> None:
    with pytest.raises(ValidationError, match="known_available_at"):
        metadata(known_available_at=NOW - timedelta(minutes=3))


def test_market_bar_rejects_impossible_ohlc_relationships() -> None:
    with pytest.raises(ValidationError, match="low cannot exceed"):
        MarketBar(
            symbol="CVX",
            interval=BarInterval.MINUTE_15,
            bar_start=NOW - timedelta(minutes=17),
            bar_end=NOW - timedelta(minutes=2),
            session_date=NOW.date(),
            exchange_timezone="America/New_York",
            open=170,
            high=171,
            low=170.5,
            close=170.25,
            volume=10,
            adjustment=PriceAdjustment.RAW,
            metadata=metadata(),
        )


def test_manifest_schema_versions_exactly_cover_capabilities() -> None:
    with pytest.raises(ValidationError, match="exactly cover"):
        ProviderManifest(
            provider_id="bad_fixture",
            display_name="Bad Fixture",
            adapter_version="1.0.0",
            capabilities=frozenset(
                {ProviderCapability.NEWS, ProviderCapability.EARNINGS}
            ),
            schema_versions={ProviderCapability.NEWS: "news_item.v1"},
        )


def test_configuration_hash_is_stable_and_contains_only_opaque_reference() -> None:
    configuration = ProviderConfiguration(
        selections={
            ProviderCapability.STRUCTURED_LLM: CapabilitySelection(
                primary=ProviderTarget(
                    provider_id="fixture_auth",
                    credential_ref="cred_fixture_token",
                )
            )
        }
    )

    rebuilt = ProviderConfiguration.model_validate(
        configuration.model_dump(mode="json")
    )

    assert rebuilt.configuration_hash == configuration.configuration_hash
    serialized = configuration.model_dump_json()
    assert "cred_fixture_token" in serialized
    assert "secret" not in serialized.casefold()


def test_selection_rejects_duplicate_primary_and_fallback() -> None:
    with pytest.raises(ValidationError, match="cannot repeat"):
        CapabilitySelection(
            primary=ProviderTarget(provider_id="fixture_recorded"),
            fallbacks=(ProviderTarget(provider_id="fixture_recorded"),),
        )


def test_packaged_example_configuration_is_valid_and_complete() -> None:
    configuration = ProviderConfiguration.from_json_file(
        ROOT / "providers.example.json"
    )

    assert set(configuration.selections) == set(ProviderCapability)
    assert len(configuration.configuration_hash) == 64
