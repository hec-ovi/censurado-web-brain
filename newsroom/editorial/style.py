"""The house style guide: the editable "model of redaction + rules".

One editorial constitution that steers BOTH the drafter and the separate evaluator
with identical rule strings. It is kept as IMMUTABLE versions plus a one-row active
pointer: an edit inserts a new version (never mutates an old one), and promote/rollback
is just moving the pointer. That gives a full audit trail and instant restore, and it
means a bad edit can be rolled back without losing the prior good version.

Fields follow the researched shape: ``voice`` is holistic prose (no length phrasing,
ever), ``exemplars`` carry labeled good AND bad samples (the bad one is the strongest
lever for a weak local drafter), ``rules`` is an ordered list of positive imperatives
each scoped to draft/eval/both and marked code- or llm-checkable, and ``lexicon`` /
``sourcing`` / ``structure`` hold the mechanically enforceable parts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = ["StyleGuide", "StyleStore"]

_JSON_LIST_FIELDS = ("exemplars", "rules")
_JSON_OBJECT_FIELDS = ("lexicon", "sourcing", "structure")


@dataclass
class StyleGuide:
    """One version of the house style. ``version`` and ``created_at`` are assigned by
    the store on insert; ``is_active`` is set on read to mark the live version."""

    voice: str = ""
    exemplars: list = field(default_factory=list)
    rules: list = field(default_factory=list)
    lexicon: dict = field(default_factory=dict)
    sourcing: dict = field(default_factory=dict)
    structure: dict = field(default_factory=dict)
    version: int = 0
    created_by: str = ""
    created_at: str = ""
    is_active: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StyleStore:
    """Versioned house-style storage. Construct with an open brain connection."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None):
        self._conn = conn
        self._clock = clock or _utcnow

    # ----- writes -----

    def add_version(self, guide: StyleGuide, *, created_by: str = "", activate: bool = False) -> StyleGuide:
        """Insert ``guide`` as a NEW immutable version and return it (with its assigned
        ``version``). Becomes active when ``activate`` is set, or automatically when no
        version is active yet (the first version is always the active one)."""
        now = self._clock().isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO style_guide (voice, exemplars, rules, lexicon, sourcing, structure, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guide.voice,
                json.dumps(guide.exemplars),
                json.dumps(guide.rules),
                json.dumps(guide.lexicon),
                json.dumps(guide.sourcing),
                json.dumps(guide.structure),
                created_by or guide.created_by,
                now,
            ),
        )
        version = int(cur.lastrowid)
        self._conn.commit()
        if activate or self.active_version() is None:
            self.promote(version)
        stored = self.get(version)
        assert stored is not None  # just inserted
        return stored

    def promote(self, version: int) -> StyleGuide:
        """Make ``version`` the active style. Raises ``KeyError`` if it does not exist.
        This is also how a rollback works: promote an older version."""
        if self.get(version) is None:
            raise KeyError(version)
        self._conn.execute(
            """
            INSERT INTO style_guide_active (id, version) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET version=excluded.version
            """,
            (version,),
        )
        self._conn.commit()
        active = self.active()
        assert active is not None  # just set
        return active

    # ----- reads -----

    def active_version(self) -> int | None:
        row = self._conn.execute("SELECT version FROM style_guide_active WHERE id = 1").fetchone()
        return int(row["version"]) if row is not None else None

    def active(self) -> StyleGuide | None:
        """The live style guide, or None when nothing has been added yet."""
        row = self._conn.execute(
            """
            SELECT g.* FROM style_guide g
            JOIN style_guide_active a ON a.version = g.version
            WHERE a.id = 1
            """
        ).fetchone()
        return _row_to_guide(row, is_active=True) if row is not None else None

    def get(self, version: int) -> StyleGuide | None:
        row = self._conn.execute(
            "SELECT * FROM style_guide WHERE version = ?", (version,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_guide(row, is_active=(version == self.active_version()))

    def list_versions(self) -> list[StyleGuide]:
        """All versions, newest first, each marked with ``is_active``."""
        active = self.active_version()
        rows = self._conn.execute("SELECT * FROM style_guide ORDER BY version DESC").fetchall()
        return [_row_to_guide(r, is_active=(r["version"] == active)) for r in rows]


def _row_to_guide(row: sqlite3.Row, *, is_active: bool) -> StyleGuide:
    return StyleGuide(
        version=int(row["version"]),
        voice=row["voice"] or "",
        exemplars=json.loads(row["exemplars"]) if row["exemplars"] else [],
        rules=json.loads(row["rules"]) if row["rules"] else [],
        lexicon=json.loads(row["lexicon"]) if row["lexicon"] else {},
        sourcing=json.loads(row["sourcing"]) if row["sourcing"] else {},
        structure=json.loads(row["structure"]) if row["structure"] else {},
        created_by=row["created_by"] or "",
        created_at=row["created_at"],
        is_active=is_active,
    )
