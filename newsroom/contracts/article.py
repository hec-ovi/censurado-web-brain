"""The publish payload as a typed Pydantic model, mirroring the vendored schema.

``PublishArticleInput`` is the article shape the platform accepts (verified field
for field against ``vendored/v1/article.schema.json``): strict (unknown fields
rejected, matching the platform's ``additionalProperties:false`` +
``DisallowUnknownFields``), ``body`` has NO maximum length (article bodies are
never truncated), and ``section`` is validated against the newsroom's own closed
enum (the platform treats section as a free string, so the newsroom pins its own
vocabulary, see ``contracts.sections``). The model carries no output-length cap:
a body of any length validates.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from newsroom.contracts.sections import SECTION_ENUM, is_valid_section

__all__ = ["PublishArticleInput"]

_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
# Mirrors article.schema.json topics.items.maxLength: a longer tag would 422 at the platform.
_TOPIC_MAXLEN = 120


def _dedupe_preserving_order(topics: list[str]) -> list[str]:
    """Drop blanks and duplicates, keeping first-seen order.

    The platform schema requires ``uniqueItems``; normalizing here means a model
    that repeats a topic does not force a finalize retry over a trivial issue."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in topics:
        topic = str(raw).strip()
        if topic and topic not in seen:
            seen.add(topic)
            out.append(topic)
    return out


class PublishArticleInput(BaseModel):
    """The full publish payload. Strict envelope (unknown fields rejected), body
    unbounded, section pinned to the newsroom enum. Consumed by finalize (Step 5)
    and the publish client (Step 7)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)  # no maximum: bodies are never truncated
    author: str = Field(min_length=1, max_length=120)
    section: str = Field(min_length=1, max_length=120)
    topics: list[str] = Field(default_factory=list)
    slug: str | None = Field(default=None, min_length=1, max_length=200, pattern=_SLUG_PATTERN)
    published_at: str | None = None
    metadata: dict | None = None

    @field_validator("section")
    @classmethod
    def _section_in_enum(cls, value: str) -> str:
        if not is_valid_section(value):
            raise ValueError(f"section {value!r} is not one of {SECTION_ENUM}")
        return value

    @field_validator("topics")
    @classmethod
    def _unique_topics(cls, value: list[str]) -> list[str]:
        out = _dedupe_preserving_order(value)
        for topic in out:
            if len(topic) > _TOPIC_MAXLEN:
                raise ValueError(
                    f"topic {topic[:60]!r} is longer than the platform's {_TOPIC_MAXLEN}-char cap")
        return out

    @field_validator("published_at")
    @classmethod
    def _rfc3339_published_at(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
        except (ValueError, TypeError):
            parsed = None
        if parsed is None or parsed.tzinfo is None or not ("T" in value or " " in value):
            raise ValueError(f"published_at {value!r} is not an RFC 3339 date-time")
        return value
