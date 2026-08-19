"""Explicit registry of packaged provider factories."""

from __future__ import annotations

from collections.abc import Callable

from investing_bot.models import ProviderCapability
from investing_bot.providers.contracts import ProviderManifest
from investing_bot.providers.protocols import BaseProvider


ProviderFactory = Callable[[], BaseProvider]


class ProviderRegistryError(ValueError):
    pass


class ProviderRegistry:
    """Register factories in code; never load provider code from writable paths."""

    def __init__(self) -> None:
        self._registrations: dict[str, tuple[ProviderManifest, ProviderFactory]] = {}

    def register(
        self, manifest: ProviderManifest, factory: ProviderFactory
    ) -> None:
        if manifest.provider_id in self._registrations:
            raise ProviderRegistryError(
                f"provider already registered: {manifest.provider_id}"
            )
        instance = factory()
        if not isinstance(instance, BaseProvider):
            raise ProviderRegistryError("factory does not implement BaseProvider")
        if instance.manifest != manifest:
            raise ProviderRegistryError("factory manifest does not match registration")
        self._registrations[manifest.provider_id] = (manifest, factory)

    def create(self, provider_id: str) -> BaseProvider:
        try:
            manifest, factory = self._registrations[provider_id]
        except KeyError as exc:
            raise ProviderRegistryError(f"unknown provider: {provider_id}") from exc
        instance = factory()
        if not isinstance(instance, BaseProvider):
            raise ProviderRegistryError("factory no longer implements BaseProvider")
        if instance.manifest != manifest:
            raise ProviderRegistryError("factory returned a changed manifest")
        return instance

    def manifest(self, provider_id: str) -> ProviderManifest:
        try:
            return self._registrations[provider_id][0]
        except KeyError as exc:
            raise ProviderRegistryError(f"unknown provider: {provider_id}") from exc

    def manifests(
        self, capability: ProviderCapability | None = None
    ) -> tuple[ProviderManifest, ...]:
        values = (registration[0] for registration in self._registrations.values())
        if capability is not None:
            values = (
                manifest for manifest in values if capability in manifest.capabilities
            )
        return tuple(sorted(values, key=lambda manifest: manifest.provider_id))
