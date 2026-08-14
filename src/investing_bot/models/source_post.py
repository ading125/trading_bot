"""Canonical representation of a social-media source post.

Provider adapters translate their external payloads into this model.  Keeping
the model provider-neutral means downstream services do not need to know
whether a post came from CivicTracker JSON, its HTML fallback, or a future
provider.
"""

from typing import Annotated, Self

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


CanonicalIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    ),
]
PostIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, pattern=r"^\S+$"),
]
Sha256Hex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


class SourcePost(BaseModel):
    """A validated, immutable social post used inside the application.

    ``provider_id`` identifies the adapter and ``platform`` identifies the
    social network on which the original post appeared.  Together,
    ``platform`` and ``post_id`` form the provider-independent post identity.

    Empty content is valid only for a media-only post.  The model deliberately
    requires timezone-aware timestamps so storage and backtesting never have
    to guess which timezone an event used.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    provider_id: CanonicalIdentifier = Field(
        description="Stable ID of the adapter that produced this record."
    )
    platform: CanonicalIdentifier = Field(
        description="Canonical source platform, for example truth_social."
    )
    post_id: PostIdentifier = Field(
        description="Stable identifier assigned to the post by its platform."
    )
    content: str = Field(
        max_length=100_000,
        description="Normalized usable post text; empty only when media-only.",
    )
    published_at: AwareDatetime = Field(
        description="Timezone-aware time at which the original post was published."
    )
    original_url: AnyHttpUrl = Field(
        description="HTTP(S) URL of the original platform post."
    )
    content_hash: Sha256Hex = Field(
        description="Lowercase SHA-256 digest of canonical content and metadata."
    )
    retrieved_at: AwareDatetime = Field(
        description="Timezone-aware time at which the provider payload was retrieved."
    )
    is_media_only: bool = Field(
        description="True when the post contains media but no usable text."
    )

    @property
    def identity(self) -> tuple[str, str]:
        """Return the stable key used to deduplicate posts across providers."""

        return (self.platform, self.post_id)

    @model_validator(mode="after")
    def validate_canonical_state(self) -> Self:
        """Reject internally inconsistent or not-yet-normalized records."""

        if "\x00" in self.content:
            raise ValueError("content must not contain null characters")

        if self.content != self.content.strip():
            raise ValueError("content must not have leading or trailing whitespace")

        if self.is_media_only and self.content:
            raise ValueError("media-only posts must not contain usable text")

        if not self.is_media_only and not self.content:
            raise ValueError("posts without usable text must be marked as media-only")

        if self.original_url.scheme != "https":
            raise ValueError("original_url must use HTTPS")

        contains_user_info = (
            self.original_url.username is not None
            or self.original_url.password is not None
        )
        if contains_user_info:
            raise ValueError("original_url must not contain user information")

        if self.published_at > self.retrieved_at:
            raise ValueError("published_at must not be later than retrieved_at")

        return self
