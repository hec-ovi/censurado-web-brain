"""The persona store: typed CRUD over the brain's ``personas`` table.

A persona is an author identity. Its ``id`` becomes ``article.author`` at the
publish seam, so it is a stable slug, derived from ``display_name`` when the caller
does not supply one. ``beat`` is validated against the harness section enum
(``contracts.sections``), the same closed vocabulary the publish path checks, so a
persona can never carry a section the platform would slugify into an orphan page.

The three JSON columns (``few_shots_pos``, ``few_shots_neg``, ``sources``) round-
trip as Python lists; the NEGATIVE few-shots are first-class, not an afterthought,
because local Gemma-class models lean on contrastive examples to hold a voice
(architecture doc, agentic-refinements appendix).

Timestamps come from an injectable ``clock`` (defaults to real UTC) so a caller, or
a test, can prove ``updated_at`` advances on edit without racing the wall clock.
This module is in-process only: it takes a ``sqlite3.Connection`` and never opens a
network.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from newsroom.contracts.sections import SECTION_ENUM, is_valid_section
from newsroom.db import open_db

__all__ = ["Persona", "PersonaStore", "open_store"]

# Persona fields a caller may change after creation. ``id`` and the timestamps are
# managed by the store, not edited directly.
_MUTABLE_FIELDS = (
    "display_name",
    "beat",
    "who_i_am",
    "about",
    "style",
    "few_shots_pos",
    "few_shots_neg",
    "sources",
    "avatar_path",
)
_JSON_FIELDS = ("few_shots_pos", "few_shots_neg", "sources")


@dataclass
class Persona:
    """One author identity. Required: ``display_name``, ``beat``, ``who_i_am``,
    ``style``. ``id`` is derived from ``display_name`` at create time when empty.
    The three list fields default to empty and are stored as JSON arrays."""

    display_name: str
    beat: str
    who_i_am: str
    style: str
    id: str = ""
    about: str = ""
    few_shots_pos: list = field(default_factory=list)
    few_shots_neg: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    avatar_path: str = ""
    created_at: str = ""
    updated_at: str = ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(text: str) -> str:
    """Lowercase alphanumerics, runs of anything else collapse to one hyphen."""
    out: list[str] = []
    prev_dash = False
    for ch in text.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


class PersonaStore:
    """Typed CRUD over ``personas``. Construct with an open brain connection
    (``newsroom.db.open_db``) or use ``open_store`` to do both at once."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None):
        self._conn = conn
        self._clock = clock or _utcnow

    # ----- writes -----

    def create(self, persona: Persona) -> Persona:
        """Insert a persona and return the stored row. Derives ``id`` from
        ``display_name`` when blank. Raises ``ValueError`` on an invalid beat, an
        underivable id, or a duplicate id."""
        if not is_valid_section(persona.beat):
            raise ValueError(f"invalid beat {persona.beat!r}; must be one of {SECTION_ENUM}")

        persona_id = persona.id.strip() or _slugify(persona.display_name)
        if not persona_id:
            raise ValueError("persona id is empty and could not be derived from display_name")

        now = self._clock().isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO personas (
                  id, display_name, beat, who_i_am, about, style,
                  few_shots_pos, few_shots_neg, sources, avatar_path,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    persona_id,
                    persona.display_name,
                    persona.beat,
                    persona.who_i_am,
                    persona.about,
                    persona.style,
                    json.dumps(persona.few_shots_pos),
                    json.dumps(persona.few_shots_neg),
                    json.dumps(persona.sources),
                    persona.avatar_path,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"persona id already exists: {persona_id}") from exc
        self._conn.commit()
        stored = self.get(persona_id)
        assert stored is not None  # just inserted
        return stored

    def update(self, persona_id: str, **changes) -> Persona:
        """Apply ``changes`` to an existing persona and bump ``updated_at``. Raises
        ``KeyError`` if the persona is missing, ``ValueError`` on an unknown field
        or an invalid beat."""
        if self.get(persona_id) is None:
            raise KeyError(persona_id)
        unknown = set(changes) - set(_MUTABLE_FIELDS)
        if unknown:
            raise ValueError(f"cannot update unknown field(s): {sorted(unknown)}")
        if "beat" in changes and not is_valid_section(changes["beat"]):
            raise ValueError(f"invalid beat {changes['beat']!r}; must be one of {SECTION_ENUM}")

        columns = list(changes)
        values = [
            json.dumps(changes[col]) if col in _JSON_FIELDS else changes[col] for col in columns
        ]
        columns.append("updated_at")
        values.append(self._clock().isoformat())
        set_clause = ", ".join(f"{col} = ?" for col in columns)
        self._conn.execute(
            f"UPDATE personas SET {set_clause} WHERE id = ?", (*values, persona_id)
        )
        self._conn.commit()
        stored = self.get(persona_id)
        assert stored is not None  # existence checked above
        return stored

    def delete(self, persona_id: str) -> bool:
        """Delete a persona. Returns True if a row was removed, False if it was
        already absent. Raises ``ValueError`` if an assignment still references the
        persona (the foreign key forbids the delete), keeping the module's error
        contract consistent rather than leaking a raw ``sqlite3.IntegrityError``."""
        try:
            cur = self._conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"cannot delete persona {persona_id!r}: it is referenced by assignments"
            ) from exc
        self._conn.commit()
        return cur.rowcount > 0

    # ----- reads -----

    def get(self, persona_id: str) -> Persona | None:
        row = self._conn.execute(
            "SELECT * FROM personas WHERE id = ?", (persona_id,)
        ).fetchone()
        return _row_to_persona(row) if row is not None else None

    def list(self, *, beat: str | None = None) -> list[Persona]:
        """All personas, oldest first, optionally filtered to one beat."""
        if beat is None:
            rows = self._conn.execute(
                "SELECT * FROM personas ORDER BY created_at, id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM personas WHERE beat = ? ORDER BY created_at, id", (beat,)
            ).fetchall()
        return [_row_to_persona(r) for r in rows]


def _row_to_persona(row: sqlite3.Row) -> Persona:
    return Persona(
        id=row["id"],
        display_name=row["display_name"],
        beat=row["beat"],
        who_i_am=row["who_i_am"],
        about=row["about"] or "",
        style=row["style"],
        few_shots_pos=json.loads(row["few_shots_pos"]) if row["few_shots_pos"] else [],
        few_shots_neg=json.loads(row["few_shots_neg"]) if row["few_shots_neg"] else [],
        sources=json.loads(row["sources"]) if row["sources"] else [],
        avatar_path=row["avatar_path"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def open_store(
    path: str | Path = ":memory:", *, clock: Callable[[], datetime] | None = None
) -> PersonaStore:
    """Open the brain DB at ``path`` and return a persona store over it."""
    return PersonaStore(open_db(path), clock=clock)
