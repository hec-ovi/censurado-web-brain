"""The prompt library: the journalist/manager/etc. prompt templates as editable versions.

The shipped ``prompts/<role>/<name>.md`` files lifted into the SAME immutable-versions plus
active-pointer shape the house style guide uses, so a prompt edit inserts a NEW version
(never mutates an old one) and promote/rollback is just moving the pointer. That gives a
full audit trail and instant restore per prompt.

UNLIKE the style guide's single-row active pointer, the active pointer here is KEYED by
the prompt's ``key`` (its relative path with forward slashes, e.g. ``journalist/research.md``),
so every key tracks its own live version independently. The store mirrors ``StyleStore``: a
typed dataclass, a store over an open ``sqlite3.Connection``, an injectable ``clock`` for
deterministic timestamps, and no network.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ["PromptTemplate", "PromptStore"]


@dataclass
class PromptTemplate:
    """One version of one prompt. ``version`` and ``created_at`` are assigned by the store
    on insert; ``is_active`` is set on read to mark the live version of that key."""

    key: str = ""
    body: str = ""
    version: int = 0
    created_by: str = ""
    created_at: str = ""
    is_active: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PromptStore:
    """Versioned prompt-template storage. Construct with an open brain connection."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None):
        self._conn = conn
        self._clock = clock or _utcnow

    # ----- writes -----

    def add_version(
        self, key: str, body: str, *, created_by: str = "", activate: bool = False
    ) -> PromptTemplate:
        """Insert ``body`` as a NEW immutable version of ``key`` and return it (with its
        assigned ``version``). Becomes active when ``activate`` is set, or automatically
        when the key has no active version yet (the first version of a key is always live).
        Raises ``ValueError`` on a blank key or a blank body (an empty prompt would silently
        neuter the stage that loads it once runs read from the store)."""
        key = (key or "").strip()
        if not key:
            raise ValueError("prompt key is required")
        if not (body or "").strip():
            raise ValueError("prompt body is required")
        now = self._clock().isoformat()
        cur = self._conn.execute(
            "INSERT INTO prompt_template (key, body, created_by, created_at) VALUES (?, ?, ?, ?)",
            (key, body, created_by, now),
        )
        version = int(cur.lastrowid)
        self._conn.commit()
        if activate or self.active_version(key) is None:
            self.promote(key, version)
        stored = self.get(version)
        assert stored is not None  # just inserted
        return stored

    def promote(self, key: str, version: int) -> PromptTemplate:
        """Make ``version`` the active version of ``key`` (this is also how a rollback works:
        promote an older version). Raises ``KeyError`` if the version does not exist or
        belongs to a different key. Raises ``ValueError`` on a blank key."""
        key = (key or "").strip()
        if not key:
            raise ValueError("prompt key is required")
        row = self._conn.execute(
            "SELECT key FROM prompt_template WHERE version = ?", (version,)
        ).fetchone()
        if row is None or row["key"] != key:
            raise KeyError((key, version))
        self._conn.execute(
            """
            INSERT INTO prompt_template_active (key, version) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET version=excluded.version
            """,
            (key, version),
        )
        self._conn.commit()
        active = self.active(key)
        assert active is not None  # just set
        return active

    # ----- reads -----

    def active_version(self, key: str) -> int | None:
        row = self._conn.execute(
            "SELECT version FROM prompt_template_active WHERE key = ?", (key,)
        ).fetchone()
        return int(row["version"]) if row is not None else None

    def active(self, key: str) -> PromptTemplate | None:
        """The live version of ``key``, or None when the key has no version yet."""
        row = self._conn.execute(
            """
            SELECT t.* FROM prompt_template t
            JOIN prompt_template_active a ON a.version = t.version
            WHERE a.key = ?
            """,
            (key,),
        ).fetchone()
        return _row_to_template(row, is_active=True) if row is not None else None

    def get(self, version: int) -> PromptTemplate | None:
        row = self._conn.execute(
            "SELECT * FROM prompt_template WHERE version = ?", (version,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_template(row, is_active=(version == self.active_version(row["key"])))

    def list_versions(self, key: str) -> list[PromptTemplate]:
        """All versions of ``key``, newest first, each marked with ``is_active``."""
        active = self.active_version(key)
        rows = self._conn.execute(
            "SELECT * FROM prompt_template WHERE key = ? ORDER BY version DESC", (key,)
        ).fetchall()
        return [_row_to_template(r, is_active=(r["version"] == active)) for r in rows]

    def list_keys(self) -> list[str]:
        """Every distinct key that has at least one version, sorted."""
        rows = self._conn.execute(
            "SELECT DISTINCT key FROM prompt_template ORDER BY key"
        ).fetchall()
        return [r["key"] for r in rows]

    def active_overrides(self) -> dict[str, str]:
        """Every key's ACTIVE body as a ``{key: body}`` map for ``load_prompt``'s
        ``overrides``: ONE join (active pointer -> version) so a run resolves the whole
        prompt library in a single query. A key with no active version is simply absent,
        so the caller falls back to the on-disk file (the B1 behavior). The store holds no
        lock; the run resolves this under the shared connection lock at the call site."""
        rows = self._conn.execute(
            """
            SELECT t.key AS key, t.body AS body FROM prompt_template t
            JOIN prompt_template_active a ON a.version = t.version
            """
        ).fetchall()
        return {row["key"]: (row["body"] or "") for row in rows}


def _row_to_template(row: sqlite3.Row, *, is_active: bool) -> PromptTemplate:
    return PromptTemplate(
        key=row["key"],
        body=row["body"] or "",
        version=int(row["version"]),
        created_by=row["created_by"] or "",
        created_at=row["created_at"],
        is_active=is_active,
    )
