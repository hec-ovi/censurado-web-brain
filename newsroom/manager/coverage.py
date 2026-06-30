"""Coverage memory: the harness's own record of what it has published, so the
manager can keep news FRESH, avoid republishing, and extend a story as it evolves
(architecture doc, coverage-memory appendix).

The harness is the SOLE publisher, so its own publish record is authoritative; it
never scrapes the platform. Each published article leaves a ``coverage`` row with
its section, headline, topics, entities, slug, and a cheap similarity FINGERPRINT
(a normalized token set over the headline, topics, and entities). The manager's
triage compares a candidate story's fingerprint against recent coverage:

  * overlap >= ``duplicate_threshold``                 -> DUPLICATE (drop)
  * ``followup_threshold`` <= overlap < duplicate      -> FOLLOW_UP (extend + cite)
  * overlap < ``followup_threshold``                   -> NEW

This is the "rules where they suffice" floor (a deterministic fingerprint and
threshold). On top of it the manager MODEL makes the final call: within the
non-duplicate bands it can demote a rules-FOLLOW_UP to NEW (a genuinely new story
that merely shares tokens) or flag a duplicate the fingerprint missed (see
``manager._resolve_triage``). The one thing the model CANNOT do is override a
rules-DUPLICATE: that floor guarantees the harness never republishes a
near-identical story even if the model misjudges.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    "CoverageItem",
    "CoverageStore",
    "CoverageVerdict",
    "fingerprint",
    "similarity",
    "classify",
]

# Short, high-frequency words that carry no story identity. Kept deliberately small:
# the fingerprint leans on entities and distinctive nouns, not an exhaustive stoplist.
_STOPWORDS = frozenset(
    """a an the of to in on for and or but with as at by from is are was were be been
    this that these those it its their his her our your you we they he she them us
    new news today say says said report reports after over into out up down off""".split()
)

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Distinctive word tokens: lowercase alphanumerics, stopwords and 1-2 char
    fragments dropped (they are noise for similarity)."""
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


def fingerprint(*, headline: str, topics=None, entities=None) -> frozenset[str]:
    """A token-set signature for a story. Tokenizes the headline and folds in each
    topic and entity both whole (so multi-word entities match exactly) and as
    tokens (so a partial mention still overlaps)."""
    toks = _tokens(headline)
    for term in list(topics or []) + list(entities or []):
        t = str(term).strip().lower()
        if not t:
            continue
        toks.add(t)
        toks |= _tokens(t)
    if not toks:
        # A headline of only stopwords and short tokens (a pure acronym like "EU",
        # "AI", "US-UN talks") would otherwise produce an EMPTY fingerprint, which
        # matches nothing -> the duplicate floor would wave the same story through
        # again. Fall back to the normalized whole headline so two identical
        # degenerate stories still collide (and a covered one is still caught).
        norm = re.sub(r"\s+", " ", headline.strip().lower())
        if norm:
            toks.add(norm)
    return frozenset(toks)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard overlap of two fingerprints in [0, 1]. Empty on either side is 0."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class CoverageItem:
    """One published article in the coverage memory."""

    section: str
    headline: str
    topics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    slug: str | None = None
    published_id: str | None = None
    assignment_id: str | None = None
    content_hash: str | None = None
    published_at: str = ""
    fingerprint_set: frozenset[str] = field(default_factory=frozenset, repr=False)

    def __post_init__(self) -> None:
        if not self.fingerprint_set:
            self.fingerprint_set = fingerprint(
                headline=self.headline, topics=self.topics, entities=self.entities
            )


@dataclass
class CoverageVerdict:
    """The triage outcome for one candidate against the coverage memory."""

    triage: "object"  # newsroom.manager.types.Triage; typed loosely to avoid a cycle
    matched: CoverageItem | None
    score: float


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CoverageStore:
    """Typed CRUD over the ``coverage`` table. In-process only (takes an open brain
    connection), like the persona and runs stores. Publish (Step 7) records a row on
    every successful publish; the manager (Step 6) reads recent rows for triage."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None):
        self._conn = conn
        self._clock = clock or _utcnow

    def record(
        self,
        *,
        section: str,
        headline: str,
        topics: list[str] | None = None,
        entities: list[str] | None = None,
        slug: str | None = None,
        published_id: str | None = None,
        assignment_id: str | None = None,
        content_hash: str | None = None,
        published_at: str | None = None,
    ) -> CoverageItem:
        """Record one published article in the coverage memory."""
        topics = list(topics or [])
        entities = list(entities or [])
        fp = fingerprint(headline=headline, topics=topics, entities=entities)
        when = published_at or self._clock().isoformat()
        self._conn.execute(
            """
            INSERT INTO coverage (
              assignment_id, published_id, slug, section, headline,
              topics, entities, fingerprint, content_hash, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assignment_id,
                published_id,
                slug,
                section,
                headline,
                json.dumps(topics),
                json.dumps(entities),
                json.dumps(sorted(fp)),  # JSON, not space-join: tokens may contain spaces
                content_hash,
                when,
            ),
        )
        self._conn.commit()
        return CoverageItem(
            section=section,
            headline=headline,
            topics=topics,
            entities=entities,
            slug=slug,
            published_id=published_id,
            assignment_id=assignment_id,
            content_hash=content_hash,
            published_at=when,
            fingerprint_set=fp,
        )

    def recent(self, *, limit: int = 50) -> list[CoverageItem]:
        """The most recent coverage, newest first (then by row id, so insertion
        order breaks ties deterministically even with equal timestamps)."""
        rows = self._conn.execute(
            "SELECT * FROM coverage ORDER BY published_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_item(r) for r in rows]


def _row_to_item(row: sqlite3.Row) -> CoverageItem:
    return CoverageItem(
        section=row["section"],
        headline=row["headline"],
        topics=json.loads(row["topics"]) if row["topics"] else [],
        entities=json.loads(row["entities"]) if row["entities"] else [],
        slug=row["slug"],
        published_id=row["published_id"],
        assignment_id=row["assignment_id"],
        content_hash=row["content_hash"],
        published_at=row["published_at"],
        fingerprint_set=frozenset(json.loads(row["fingerprint"]) if row["fingerprint"] else []),
    )


def classify(
    fp: frozenset[str],
    coverage: list[CoverageItem],
    *,
    duplicate_threshold: float,
    followup_threshold: float,
) -> CoverageVerdict:
    """Rules-first triage of one candidate fingerprint against recent coverage.

    Returns the BEST-matching coverage item and the band its overlap falls in.
    Imported lazily to avoid a types<->coverage import cycle."""
    from newsroom.manager.types import Triage

    best: CoverageItem | None = None
    best_score = 0.0
    for item in coverage:
        s = similarity(fp, item.fingerprint_set)
        if s > best_score:
            best_score, best = s, item

    if best is None or best_score < followup_threshold:
        return CoverageVerdict(Triage.NEW, None, best_score)
    if best_score >= duplicate_threshold:
        return CoverageVerdict(Triage.DUPLICATE, best, best_score)
    return CoverageVerdict(Triage.FOLLOW_UP, best, best_score)
