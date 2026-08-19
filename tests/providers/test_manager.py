from __future__ import annotations

import pytest

from investing_bot.models import NewsRequest, ProviderCapability
from investing_bot.providers import (
    CapabilitySelection,
    CredentialPresenceStore,
    ProviderCallError,
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderManager,
    ProviderRegistry,
    ProviderTarget,
)
from investing_bot.providers.contracts import ProviderErrorCode, ProviderHealthState
from investing_bot.providers.fixtures import (
    RecordedFixtureProvider,
    build_fixture_manifest,
)
from investing_bot.providers.registry import ProviderRegistryError


def register_fixture(
    registry: ProviderRegistry,
    provider_id: str,
    **options: object,
) -> None:
    authentication_required = bool(options.get("authentication_required", False))
    capabilities = options.get("capabilities")
    manifest = build_fixture_manifest(
        provider_id,
        authentication_required=authentication_required,
        capabilities=capabilities if isinstance(capabilities, frozenset) else None,
    )
    registry.register(
        manifest,
        lambda: RecordedFixtureProvider(provider_id, **options),
    )


def manager_with(
    registry: ProviderRegistry,
    capability: ProviderCapability,
    primary: ProviderTarget,
    *fallbacks: ProviderTarget,
    credentials: CredentialPresenceStore | None = None,
) -> ProviderManager:
    return ProviderManager(
        registry=registry,
        configuration=ProviderConfiguration(
            selections={
                capability: CapabilitySelection(
                    primary=primary,
                    fallbacks=tuple(fallbacks),
                )
            }
        ),
        credentials=credentials or CredentialPresenceStore(),
    )


@pytest.mark.anyio
async def test_configuration_switches_provider_without_business_code_changes() -> None:
    registry = ProviderRegistry()
    register_fixture(registry, "fixture_one")
    register_fixture(registry, "fixture_two")

    first = manager_with(
        registry,
        ProviderCapability.NEWS,
        ProviderTarget(provider_id="fixture_one"),
    )
    second = manager_with(
        registry,
        ProviderCapability.NEWS,
        ProviderTarget(provider_id="fixture_two"),
    )

    first_result = await (
        await first.pin(ProviderCapability.NEWS, run_id="same-business-call")
    ).fetch_news(NewsRequest(symbols=("CVX",)))
    second_result = await (
        await second.pin(ProviderCapability.NEWS, run_id="same-business-call")
    ).fetch_news(NewsRequest(symbols=("CVX",)))

    assert first_result.items[0].headline == second_result.items[0].headline
    assert first_result.provenance.provider_id == "fixture_one"
    assert second_result.provenance.provider_id == "fixture_two"
    assert first_result.provenance.dataset_lineage != second_result.provenance.dataset_lineage


@pytest.mark.anyio
async def test_unhealthy_primary_falls_back_before_run_is_pinned() -> None:
    registry = ProviderRegistry()
    register_fixture(
        registry,
        "fixture_unhealthy",
        health_overrides={
            ProviderCapability.NEWS: (
                ProviderHealthState.UNHEALTHY,
                ProviderErrorCode.TRANSPORT,
            )
        },
    )
    register_fixture(registry, "fixture_backup")
    manager = manager_with(
        registry,
        ProviderCapability.NEWS,
        ProviderTarget(provider_id="fixture_unhealthy"),
        ProviderTarget(provider_id="fixture_backup"),
    )

    pinned = await manager.pin(ProviderCapability.NEWS, run_id="fallback-run")
    result = await pinned.fetch_news(NewsRequest(symbols=("CVX",)))

    assert pinned.provider_id == "fixture_backup"
    assert result.provenance.provider_id == "fixture_backup"
    assert len(result.provenance.fallback_attempts) == 1
    assert result.provenance.fallback_attempts[0].provider_id == "fixture_unhealthy"
    assert result.provenance.fallback_attempts[0].error_code == "transport"


@pytest.mark.anyio
async def test_pinned_provider_does_not_switch_after_work_begins() -> None:
    registry = ProviderRegistry()
    register_fixture(
        registry,
        "fixture_fails_call",
        failure_overrides={ProviderCapability.NEWS: ProviderErrorCode.TRANSPORT},
    )
    register_fixture(registry, "fixture_backup")
    manager = manager_with(
        registry,
        ProviderCapability.NEWS,
        ProviderTarget(provider_id="fixture_fails_call"),
        ProviderTarget(provider_id="fixture_backup"),
    )

    pinned = await manager.pin(ProviderCapability.NEWS, run_id="pinned-run")
    with pytest.raises(ProviderCallError) as caught:
        await pinned.fetch_news(NewsRequest(symbols=("CVX",)))

    assert pinned.provider_id == "fixture_fails_call"
    assert caught.value.failure.code is ProviderErrorCode.TRANSPORT


@pytest.mark.anyio
async def test_locked_credential_can_fall_back_without_exposing_secret() -> None:
    registry = ProviderRegistry()
    register_fixture(registry, "fixture_auth", authentication_required=True)
    register_fixture(registry, "fixture_public")
    manager = manager_with(
        registry,
        ProviderCapability.NEWS,
        ProviderTarget(
            provider_id="fixture_auth", credential_ref="cred_fixture_missing"
        ),
        ProviderTarget(provider_id="fixture_public"),
    )

    pinned = await manager.pin(ProviderCapability.NEWS, run_id="locked-run")

    assert pinned.provider_id == "fixture_public"
    health = manager.health_snapshot()
    locked = next(item for item in health if item.provider_id == "fixture_auth")
    assert locked.state is ProviderHealthState.LOCKED
    assert locked.message == "configured credential is locked or unavailable"
    assert "cred_fixture_missing" not in locked.model_dump_json()


@pytest.mark.anyio
async def test_unhealthy_capability_does_not_disable_unrelated_capability() -> None:
    registry = ProviderRegistry()
    register_fixture(
        registry,
        "fixture_partial_health",
        health_overrides={
            ProviderCapability.SOCIAL_POSTS: (
                ProviderHealthState.UNHEALTHY,
                ProviderErrorCode.TEMPORARILY_UNAVAILABLE,
            )
        },
    )
    configuration = ProviderConfiguration(
        selections={
            ProviderCapability.NEWS: CapabilitySelection(
                primary=ProviderTarget(provider_id="fixture_partial_health")
            ),
            ProviderCapability.SOCIAL_POSTS: CapabilitySelection(
                primary=ProviderTarget(provider_id="fixture_partial_health")
            ),
        }
    )
    manager = ProviderManager(
        registry=registry,
        configuration=configuration,
        credentials=CredentialPresenceStore(),
    )
    await manager.refresh_health()

    news = await manager.pin(ProviderCapability.NEWS, run_id="news-still-ready")
    assert news.provider_id == "fixture_partial_health"
    with pytest.raises(ProviderCallError):
        await manager.pin(ProviderCapability.SOCIAL_POSTS, run_id="social-down")


def test_configuration_rejects_provider_without_selected_capability() -> None:
    registry = ProviderRegistry()
    register_fixture(
        registry,
        "fixture_news_only",
        capabilities=frozenset({ProviderCapability.NEWS}),
    )

    with pytest.raises(ProviderConfigurationError, match="does not support"):
        manager_with(
            registry,
            ProviderCapability.EARNINGS,
            ProviderTarget(provider_id="fixture_news_only"),
        )


def test_registry_rejects_factory_with_changed_manifest() -> None:
    registry = ProviderRegistry()
    expected = build_fixture_manifest("fixture_expected")

    with pytest.raises(ProviderRegistryError, match="manifest"):
        registry.register(
            expected,
            lambda: RecordedFixtureProvider("fixture_different"),
        )
