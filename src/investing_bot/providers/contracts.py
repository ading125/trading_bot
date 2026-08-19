"""Provider manifests, health, configuration, and sanitized failures."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from investing_bot.models import ProviderCapability, QuotaStatus


ProviderId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    ),
]
CredentialReference = Annotated[
    str,
    StringConstraints(
        min_length=8,
        max_length=120,
        pattern=r"^cred_[a-z0-9]+(?:_[a-z0-9]+)*$",
    ),
]


class ProviderErrorCode(StrEnum):
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"
    STALE = "stale"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    TRANSPORT = "transport"
    UNSUPPORTED = "unsupported"


class ProviderHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    LOCKED = "locked"
    UNSUPPORTED = "unsupported"


class RateLimitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requests: int = Field(ge=1)
    window_seconds: int = Field(ge=1)


class ProviderManifest(BaseModel):
    """Static, packaged declaration of one adapter's supported contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    display_name: str = Field(min_length=1, max_length=120)
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    capabilities: frozenset[ProviderCapability] = Field(min_length=1)
    schema_versions: dict[ProviderCapability, str]
    authentication_required: bool = False
    credential_kind: str | None = Field(default=None, max_length=80)
    rate_limit: RateLimitPolicy | None = None
    supported_intervals: frozenset[str] = frozenset()
    optional_features: frozenset[str] = frozenset()
    allowed_origins: tuple[str, ...] = ()
    fixture: bool = False

    @model_validator(mode="after")
    def validate_manifest_contract(self) -> Self:
        if set(self.schema_versions) != set(self.capabilities):
            raise ValueError("schema_versions must exactly cover capabilities")
        if self.authentication_required and not self.credential_kind:
            raise ValueError("authenticated providers require credential_kind")
        if not self.authentication_required and self.credential_kind is not None:
            raise ValueError("credential_kind is valid only when authentication is required")
        if any(not origin.startswith("https://") for origin in self.allowed_origins):
            raise ValueError("provider origins must use HTTPS")
        return self


class ProviderFailure(BaseModel):
    """Sanitized provider failure safe for logs, APIs, and fallback decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    capability: ProviderCapability
    code: ProviderErrorCode
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool
    retry_after_seconds: float | None = Field(default=None, ge=0)


class ProviderCallError(RuntimeError):
    """Exception wrapper that intentionally exposes only ``ProviderFailure``."""

    def __init__(self, failure: ProviderFailure) -> None:
        super().__init__(failure.safe_message)
        self.failure = failure


class ProviderContractError(RuntimeError):
    """Raised when an adapter violates its declared canonical contract."""


class ConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    capability: ProviderCapability
    state: ProviderHealthState
    checked_at: AwareDatetime
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    error_code: ProviderErrorCode | None = None
    message: str | None = Field(default=None, max_length=500)
    quota: QuotaStatus | None = None

    @model_validator(mode="after")
    def validate_health_details(self) -> Self:
        if self.state is ProviderHealthState.HEALTHY and self.error_code is not None:
            raise ValueError("healthy status cannot include an error code")
        if self.state in {
            ProviderHealthState.UNHEALTHY,
            ProviderHealthState.LOCKED,
            ProviderHealthState.UNSUPPORTED,
        } and self.error_code is None:
            raise ValueError("unavailable status requires an error code")
        return self

    @property
    def available(self) -> bool:
        return self.state in {
            ProviderHealthState.HEALTHY,
            ProviderHealthState.DEGRADED,
        }


class ProviderTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: ProviderId
    credential_ref: CredentialReference | None = None


class CapabilitySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    primary: ProviderTarget
    fallbacks: tuple[ProviderTarget, ...] = ()

    @model_validator(mode="after")
    def unique_provider_ids(self) -> Self:
        ids = [self.primary.provider_id, *(item.provider_id for item in self.fallbacks)]
        if len(set(ids)) != len(ids):
            raise ValueError("provider selection cannot repeat a provider")
        return self

    @property
    def ordered_targets(self) -> tuple[ProviderTarget, ...]:
        return (self.primary, *self.fallbacks)


class ProviderConfiguration(BaseModel):
    """Validated per-capability provider choices stored without secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    selections: dict[ProviderCapability, CapabilitySelection] = Field(min_length=1)

    @property
    def configuration_hash(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return sha256(encoded).hexdigest()

    @classmethod
    def from_json_file(cls, path: Path) -> ProviderConfiguration:
        """Read a local non-secret selection document with strict validation."""

        return cls.model_validate_json(path.read_text(encoding="utf-8"))
