"""Packaged provider registrations and local selection loading."""

from __future__ import annotations

from pathlib import Path

from investing_bot.models import ProviderCapability
from investing_bot.providers.contracts import (
    CapabilitySelection,
    ProviderConfiguration,
    ProviderTarget,
)
from investing_bot.providers.credentials import (
    CredentialPresenceStore,
    CredentialReferenceStore,
)
from investing_bot.providers.fixtures import (
    RecordedFixtureProvider,
    build_fixture_manifest,
)
from investing_bot.providers.civictracker import (
    CivicTrackerHtmlProvider,
    CivicTrackerProvider,
    build_html_manifest,
    build_json_manifest,
)
from investing_bot.providers.registry import ProviderRegistry
from investing_bot.providers.groq import GroqStructuredLLMProvider, build_groq_manifest
from investing_bot.providers.yahoo import YahooFinanceProvider, build_yahoo_manifest


PRIMARY_FIXTURE_ID = "fixture_recorded"
BACKUP_FIXTURE_ID = "fixture_backup"


def build_default_registry(
    *,
    civictracker_member_uuid: str = "3094abf7-4a95-4b8d-8c8d-af7d1c3747a1",
    civictracker_timeout_seconds: float = 15.0,
    civictracker_retries: int = 2,
    yahoo_raw_cache_dir: Path | None = None,
    yahoo_repair: bool = False,
    yahoo_timeout_seconds: float = 15.0,
    yahoo_retries: int = 2,
    credentials: CredentialReferenceStore | None = None,
    groq_timeout_seconds: float = 60.0,
    groq_retries: int = 2,
) -> ProviderRegistry:
    """Register only providers compiled into this application version."""

    registry = ProviderRegistry()
    for provider_id in (PRIMARY_FIXTURE_ID, BACKUP_FIXTURE_ID):
        manifest = build_fixture_manifest(provider_id)
        registry.register(
            manifest,
            lambda provider_id=provider_id: RecordedFixtureProvider(provider_id),
        )
    registry.register(
        build_json_manifest(),
        lambda: CivicTrackerProvider(
            member_uuid=civictracker_member_uuid,
            timeout_seconds=civictracker_timeout_seconds,
            retries=civictracker_retries,
        ),
    )
    registry.register(
        build_html_manifest(),
        lambda: CivicTrackerHtmlProvider(
            member_uuid=civictracker_member_uuid,
            timeout_seconds=civictracker_timeout_seconds,
        ),
    )
    yahoo_provider = YahooFinanceProvider(
        raw_cache_dir=yahoo_raw_cache_dir,
        repair=yahoo_repair,
        timeout_seconds=yahoo_timeout_seconds,
        retries=yahoo_retries,
    )
    registry.register(build_yahoo_manifest(), lambda: yahoo_provider)
    credential_store = credentials or CredentialPresenceStore()
    registry.register(
        build_groq_manifest(),
        lambda: GroqStructuredLLMProvider(
            credential_store,
            timeout_seconds=groq_timeout_seconds,
            retries=groq_retries,
        ),
    )
    return registry


def default_provider_configuration(
    *,
    live_social: bool = False,
    live_market: bool = False,
    live_analysis: bool = False,
) -> ProviderConfiguration:
    """Use live CivicTracker only in non-test runtime environments."""

    selections = {
            capability: CapabilitySelection(
                primary=ProviderTarget(provider_id=PRIMARY_FIXTURE_ID),
                fallbacks=(ProviderTarget(provider_id=BACKUP_FIXTURE_ID),),
            )
            for capability in ProviderCapability
        }
    if live_social:
        selections[ProviderCapability.SOCIAL_POSTS] = CapabilitySelection(
            primary=ProviderTarget(provider_id="civictracker_json"),
            fallbacks=(ProviderTarget(provider_id="civictracker_html"),),
        )
    if live_market:
        for capability in (
            ProviderCapability.DAILY_BARS,
            ProviderCapability.INTRADAY_BARS,
            ProviderCapability.QUOTES,
            ProviderCapability.CORPORATE_ACTIONS,
            ProviderCapability.SYMBOL_LOOKUP,
            ProviderCapability.NEWS,
            ProviderCapability.EARNINGS,
        ):
            selections[capability] = CapabilitySelection(
                primary=ProviderTarget(provider_id="yahoo_finance"),
                fallbacks=(ProviderTarget(provider_id=PRIMARY_FIXTURE_ID),),
            )
    if live_analysis:
        selections[ProviderCapability.STRUCTURED_LLM] = CapabilitySelection(
            primary=ProviderTarget(
                provider_id="groq",
                credential_ref="cred_groq",
            ),
            fallbacks=(ProviderTarget(provider_id=PRIMARY_FIXTURE_ID),),
        )
    return ProviderConfiguration(selections=selections)


def load_provider_configuration(
    path: Path,
    *,
    live_social: bool = False,
    live_market: bool = False,
    live_analysis: bool = False,
) -> ProviderConfiguration:
    """Load an optional local selection file, falling back to recorded fixtures."""

    if not path.exists():
        return default_provider_configuration(
            live_social=live_social,
            live_market=live_market,
            live_analysis=live_analysis,
        )
    if not path.is_file():
        raise ValueError("provider configuration path must be a regular file")
    return ProviderConfiguration.from_json_file(path)
