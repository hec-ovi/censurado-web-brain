"""The author WRITE payload as a typed Pydantic model, mirroring the vendored schema.

``AuthorInput`` is the shape the backend accepts at ``POST /authors`` (upsert by
handle), verified field for field against ``vendored/v1/authors.schema.json`` and,
through it, against the backend's Go ``authorInput``. It is the author analog of
``contracts.article.PublishArticleInput``.

Strict envelope (unknown fields rejected, matching the backend's ``decodeStrict`` +
the schema's ``additionalProperties:false``). Only ``handle`` is required; every other
field defaults empty. ``sources`` is TRISTATE: ``None`` means "leave the attached-source
join untouched", an empty list means "detach all", a non-empty list replaces the set.
The read-only server fields (``deleted``, ``created_at``, ``updated_at``) are NOT part of
this write shape; project a read record onto these keys before validating or re-writing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AuthorInput", "AUTHOR_WRITE_FIELDS", "author_write_payload"]

# The exact write-shape keys, in schema order. Used to project a READ record (which also
# carries deleted/created_at/updated_at) down to the write payload the contract accepts.
AUTHOR_WRITE_FIELDS: tuple[str, ...] = (
    "handle", "name", "bio", "avatar", "gender", "about", "style", "topics", "sources", "metadata",
)


class AuthorInput(BaseModel):
    """The author upsert payload. Strict envelope; only handle is required."""

    model_config = ConfigDict(extra="forbid")

    handle: str = Field(min_length=1, max_length=120)
    name: str = Field(default="", max_length=200)
    bio: str = ""
    avatar: str = ""
    gender: str = ""
    about: str = ""
    style: str = ""
    topics: list[str] = Field(default_factory=list)
    sources: list[str] | None = None  # tristate: None = leave join untouched
    metadata: dict | None = None


def author_write_payload(record: dict) -> dict:
    """Project a full author READ record (which also carries deleted/created_at/updated_at)
    onto just the write-shape keys, so it can be validated with ``AuthorInput`` or re-sent to
    ``POST /authors`` without the strict envelope rejecting a server-owned field. Keys absent
    from the record are simply omitted."""
    return {k: record[k] for k in AUTHOR_WRITE_FIELDS if k in record}
