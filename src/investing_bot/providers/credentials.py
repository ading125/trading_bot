"""Opaque credential-presence boundary; usable secrets never enter config."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from investing_bot.providers.contracts import CredentialReference


class CredentialReferenceStore(Protocol):
    """Report whether an encrypted credential reference is configured."""

    def is_configured(self, reference: CredentialReference) -> bool: ...


class CredentialPresenceStore:
    """Non-secret reference set used until encrypted storage is implemented."""

    def __init__(self, references: Iterable[CredentialReference] = ()) -> None:
        self._references = frozenset(references)

    def is_configured(self, reference: CredentialReference) -> bool:
        return reference in self._references
