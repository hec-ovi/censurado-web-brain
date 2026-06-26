"""The manager's typed vocabulary: candidate stories, the triage verdict, the
assignment specs it emits, and the manifest that wraps them.

These are plain artifacts (A.5/A.7): the manager produces a ``Manifest`` of
``AssignmentSpec`` rows; the deterministic fan-out dispatch turns each spec into a
real ``assignments`` row and runs the per-article pipeline for it. The manager is
the SOLE fan-out point, so this is the one place a "how many articles" decision is
made, and it is clamped to ``N_MAX``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = ["Triage", "Candidate", "AssignmentSpec", "Manifest"]


class Triage(str, Enum):
    """How a candidate story relates to what the harness has already published.

    * ``NEW``       a story we have not covered -> a normal assignment.
    * ``FOLLOW_UP`` a story we have covered that has new developments -> assign with
                    the prior article attached, cover only what is new, and cite it.
    * ``DUPLICATE`` the same event with no new information -> DROP, never republish.
    """

    NEW = "new"
    FOLLOW_UP = "follow_up"
    DUPLICATE = "duplicate"


@dataclass
class Candidate:
    """A candidate story the manager surfaced from a news search. ``section`` is the
    beat it most plausibly belongs to (may be empty when a raw search hit gives no
    hint); ``entities`` are the people/orgs/places the story is about, used by the
    coverage fingerprint."""

    headline: str
    section: str = ""
    url: str = ""
    snippet: str = ""
    entities: list[str] = field(default_factory=list)


@dataclass
class AssignmentSpec:
    """One assignment the manager emits: WHO writes (``persona_id``), in which
    ``section`` (authoritative: the persona's own beat), and the ``angle`` brief.
    ``triage`` records the coverage decision that produced it; a ``FOLLOW_UP`` also
    carries the ``follow_up_slug`` of the prior article it extends. ``entities`` are the
    people/orgs/places the story is about: they ride all the way to the published
    article's ``coverage`` row so the NEXT run's de-dup fingerprint compares the same
    entity set the manager triaged on (without them the entity channel is dead and a
    reworded headline about the same event slips past the duplicate floor)."""

    persona_id: str
    section: str
    angle: str
    triage: Triage = Triage.NEW
    follow_up_slug: str | None = None
    headline: str = ""
    entities: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    """The manager's output: the clamped, triaged list of assignments plus why the
    loop stopped. ``forced`` is True when a terminal was forced (the step cap or the
    circuit breaker fired and the manager degraded to whatever it had ranked rather
    than failing the run)."""

    assignments: list[AssignmentSpec]
    stop_reason: str = "emitted"  # emitted | max_steps | circuit_breaker | tool_budget | budget_exhausted
    forced: bool = False
    steps: int = 0
    dropped_duplicates: int = 0
    candidates_considered: int = 0
