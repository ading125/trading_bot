"""Adapters that translate external provider payloads into domain models.

Provider-specific response shapes must stop at this package boundary.  Code in
the rest of the application should consume models from :mod:`investing_bot.models`.
"""

from investing_bot.providers.bootstrap import (
    build_default_registry,
    default_provider_configuration,
    load_provider_configuration,
)
from investing_bot.providers.contracts import (
    CapabilitySelection,
    ConnectionTestResult,
    ProviderCallError,
    ProviderConfiguration,
    ProviderContractError,
    ProviderErrorCode,
    ProviderHealthState,
    ProviderManifest,
    ProviderTarget,
)
from investing_bot.providers.credentials import (
    CredentialNotFoundError,
    CredentialPresenceStore,
    CredentialReferenceStore,
    CredentialUnlockError,
    CredentialVaultError,
    CredentialVaultLockedError,
    CredentialVaultStatus,
    EncryptedCredentialStore,
)
from investing_bot.providers.manager import (
    PinnedProvider,
    ProviderConfigurationError,
    ProviderManager,
)
from investing_bot.providers.registry import ProviderRegistry

__all__ = [
    "CapabilitySelection",
    "ConnectionTestResult",
    "CredentialPresenceStore",
    "CredentialReferenceStore",
    "CredentialNotFoundError",
    "CredentialUnlockError",
    "CredentialVaultError",
    "CredentialVaultLockedError",
    "CredentialVaultStatus",
    "EncryptedCredentialStore",
    "PinnedProvider",
    "ProviderCallError",
    "ProviderConfiguration",
    "ProviderConfigurationError",
    "ProviderContractError",
    "ProviderErrorCode",
    "ProviderHealthState",
    "ProviderManager",
    "ProviderManifest",
    "ProviderRegistry",
    "ProviderTarget",
    "build_default_registry",
    "default_provider_configuration",
    "load_provider_configuration",
]
