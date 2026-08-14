"""Classify source-post observations as new, duplicate, or edited.

This first implementation deliberately keeps its state in memory.  A later
repository can persist the same decisions without changing callers because
the service only consumes the canonical :class:`SourcePost` model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from investing_bot.models import SourcePost


PostIdentity = tuple[str, str]
"""The canonical ``(platform, post_id)`` key for one source post."""


class DeduplicationOutcome(StrEnum):
    """Possible classifications for an observed source post."""

    NEW = "new"
    DUPLICATE = "duplicate"
    EDITED = "edited"


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    """The decision made while processing one source-post observation.

    ``current`` is always the authoritative record retained by the service.
    ``previous`` is populated only for an edit, allowing a caller to store or
    display the revision without having to query mutable service state.
    """

    outcome: DeduplicationOutcome
    current: SourcePost
    previous: SourcePost | None = None

    @property
    def creates_event(self) -> bool:
        """Return whether downstream code should emit a new ingestion event."""

        return self.outcome is not DeduplicationOutcome.DUPLICATE


class SourcePostDeduplicator:
    """Track the current revision of each post for one process lifetime.

    Identity answers "is this the same post?" while ``content_hash`` answers
    "did that post change?".  Re-observing the same hash does not replace the
    stored record, so a later retrieval timestamp cannot create a false event.
    """

    def __init__(self) -> None:
        self._current_by_identity: dict[PostIdentity, SourcePost] = {}

    def process(self, post: SourcePost) -> DeduplicationResult:
        """Classify ``post`` and update state when it is new or edited."""

        previous = self._current_by_identity.get(post.identity)

        if previous is None:
            self._current_by_identity[post.identity] = post
            return DeduplicationResult(
                outcome=DeduplicationOutcome.NEW,
                current=post,
            )

        if previous.content_hash == post.content_hash:
            return DeduplicationResult(
                outcome=DeduplicationOutcome.DUPLICATE,
                current=previous,
            )

        self._current_by_identity[post.identity] = post
        return DeduplicationResult(
            outcome=DeduplicationOutcome.EDITED,
            current=post,
            previous=previous,
        )

    def get(self, identity: PostIdentity) -> SourcePost | None:
        """Return the current record for ``identity``, if it has been seen."""

        return self._current_by_identity.get(identity)

    def __len__(self) -> int:
        """Return the number of distinct post identities currently tracked."""

        return len(self._current_by_identity)
