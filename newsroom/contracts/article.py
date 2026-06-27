"""The publish payload as typed Pydantic models, mirroring the vendored schema.

``PublishArticleInput`` is the article shape the platform accepts (verified field
for field against ``vendored/v1/article.schema.json``): strict (unknown fields
rejected, matching the platform's ``additionalProperties:false`` +
``DisallowUnknownFields``), ``body`` has NO maximum length (article bodies are
never truncated), and ``section`` is validated against the harness's own closed
enum (the platform treats section as a free string, so the harness pins its own
vocabulary, see ``contracts.sections``).

``FinalizedDraft`` is the smaller shape the FINALIZE model authors: the content
fields a journalist legitimately writes (title, body, topics, optional slug).
``author`` and ``section`` are NOT model-chosen, they are the persona id and the
assignment's section, set deterministically by the pipeline; this keeps a
hallucinated byline or a stray section out of the published payload by
construction. The pipeline assembles a ``PublishArticleInput`` from a validated
``FinalizedDraft`` plus those authoritative fields.

Neither model carries any output-length cap; structured output at the finalize
seam comes from validate-and-retry (pydantic-ai), never from truncation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from newsroom.contracts.sections import SECTION_ENUM, is_valid_section

__all__ = ["FinalizedDraft", "PublishArticleInput"]

_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


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


class FinalizedDraft(BaseModel):
    """The content fields the finalize model authors. Validated by pydantic-ai;
    a parse or validation failure triggers exactly one re-prompt (B.3).

    Tolerant of EXTRA keys (``extra="ignore"``): a chatty local model that also
    echoes ``author`` or ``section`` (which the pipeline sets authoritatively, not
    the model) is dropped rather than failing validation and burning the retry."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=300)
    # The attention-staged dek and standalone summary. Optional with an empty
    # default so a model that omits them does not burn the one finalize retry;
    # the finalize prompt still asks for them firmly. The pipeline stamps them
    # into metadata.subtitle / metadata.description only when non-empty (the
    # public generator renders those as the card/article subtitle + standfirst).
    subtitle: str = Field(default="", max_length=400)
    summary: str = Field(default="", max_length=1000)
    body: str = Field(min_length=1)  # no maximum: bodies are never truncated
    topics: list[str] = Field(default_factory=list)
    slug: str | None = Field(default=None, min_length=1, max_length=200, pattern=_SLUG_PATTERN)

    @field_validator("topics")
    @classmethod
    def _unique_topics(cls, value: list[str]) -> list[str]:
        return _dedupe_preserving_order(value)


class PublishArticleInput(BaseModel):
    """The full publish payload. Strict envelope (unknown fields rejected), body
    unbounded, section pinned to the harness enum. Consumed by finalize (Step 5)
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
        return _dedupe_preserving_order(value)
