"""Typed CRUD over the ``runs`` and ``assignments`` tables (brain-owned SQLite).

In-process only, like the persona store: it takes an open ``sqlite3.Connection``
and never opens a network. Timestamps come from an injectable ``clock`` so a test
can prove ordering without racing the wall clock.

The assignment lifecycle this store models:

  assigned   the manager created it, nothing drafted yet
  drafting   the per-article pipeline is running
  ready      finalized: ``final_body`` + ``content_hash`` + ``idempotency_key`` are
             persisted (so a post-finalize crash replays the byte-identical body),
             awaiting the publish POST
  published  the platform accepted it (Step 7), ``published_id`` set
  publish_failed  finalized but the platform REJECTED the publish (e.g. a 403 or a
             transport fault); the body is kept so it stays re-publishable, and
             ``drop_reason`` carries the publish code so the failure is observable
  dropped    abandoned with a ``drop_reason`` (e.g. ``budget_exhausted``); the
             ledger digest is kept for audit, the body is NEVER published

``ready`` is the one state added beyond the architecture doc's
``assigned|drafting|published|dropped`` sketch: it is the gap between "finalized"
and "POSTed", and persisting the body there is exactly what makes the publish
idempotent across a crash (B.0). The ``assignments.status`` column has no CHECK,
so this is a free, non-breaking addition.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from newsroom.db import open_db

__all__ = ["Assignment", "Run", "RunStore", "open_runs_store"]

_RUN_MODES = ("manual", "express", "managed")


@dataclass
class Run:
    id: str
    mode: str
    status: str
    n_requested: int | None
    created_at: str
    finished_at: str | None = None


@dataclass
class Assignment:
    id: str
    run_id: str
    persona_id: str
    section: str
    angle: str
    status: str
    created_at: str
    drop_reason: str | None = None
    final_body: str | None = None
    content_hash: str | None = None
    idempotency_key: str | None = None
    ledger_digest: str | None = None
    published_id: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStore:
    """Typed CRUD over ``runs`` and ``assignments``. Construct with an open brain
    connection (``newsroom.db.open_db``) or use ``open_runs_store``."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None):
        self._conn = conn
        self._clock = clock or _utcnow

    # ----- runs -----

    def create_run(self, *, mode: str, n_requested: int | None = None, id: str | None = None,
                   status: str = "running") -> Run:
        if mode not in _RUN_MODES:
            raise ValueError(f"invalid run mode {mode!r}; must be one of {_RUN_MODES}")
        run_id = (id or "").strip() or uuid.uuid4().hex
        now = self._clock().isoformat()
        try:
            self._conn.execute(
                "INSERT INTO runs (id, mode, status, n_requested, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, mode, status, n_requested, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"run id already exists: {run_id}") from exc
        self._conn.commit()
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> Run | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row is not None else None

    def finish_run(self, run_id: str, *, status: str = "done") -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, self._clock().isoformat(), run_id),
        )
        self._conn.commit()

    # ----- assignments -----

    def create_assignment(self, *, run_id: str, persona_id: str, section: str, angle: str = "",
                          id: str | None = None, status: str = "assigned") -> Assignment:
        assignment_id = (id or "").strip() or uuid.uuid4().hex
        now = self._clock().isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO assignments (id, run_id, persona_id, section, angle, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (assignment_id, run_id, persona_id, section, angle, status, now),
            )
        except sqlite3.IntegrityError as exc:
            # A bad run_id / persona_id (foreign key) or a duplicate id both land here.
            raise ValueError(f"cannot create assignment {assignment_id!r}: {exc}") from exc
        self._conn.commit()
        return self.get_assignment(assignment_id)  # type: ignore[return-value]

    def get_assignment(self, assignment_id: str) -> Assignment | None:
        row = self._conn.execute(
            "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        return _row_to_assignment(row) if row is not None else None

    def list_assignments(self, *, run_id: str) -> list[Assignment]:
        rows = self._conn.execute(
            "SELECT * FROM assignments WHERE run_id = ? ORDER BY created_at, id", (run_id,)
        ).fetchall()
        return [_row_to_assignment(r) for r in rows]

    def mark_drafting(self, assignment_id: str) -> None:
        self._set(assignment_id, status="drafting")

    def mark_dropped(self, assignment_id: str, *, reason: str, ledger_digest: str | None = None) -> None:
        """Abandon an assignment. The body is never published; the ledger digest is
        kept for audit so a dropped run is still traceable."""
        self._set(assignment_id, status="dropped", drop_reason=reason, ledger_digest=ledger_digest)

    def finalize_assignment(self, assignment_id: str, *, final_body: str, content_hash: str,
                            idempotency_key: str, ledger_digest: str) -> None:
        """Persist the finalized article and move to ``ready`` (awaiting publish).
        Storing the body BEFORE the POST is what makes the publish idempotent across
        a crash: the same body replays to the same content hash and key (B.0)."""
        self._set(
            assignment_id,
            status="ready",
            final_body=final_body,
            content_hash=content_hash,
            idempotency_key=idempotency_key,
            ledger_digest=ledger_digest,
        )

    def mark_publish_failed(self, assignment_id: str, *, reason: str) -> None:
        """A finalized article the platform REJECTED at publish (e.g. a 403 or a
        transport fault). Distinct from ``ready`` (finalized, publish not yet
        attempted) so a failed publish is observable rather than indistinguishable
        from publish-pending. The body and content hash are KEPT (not cleared), so
        the article stays re-publishable; ``drop_reason`` carries the publish code."""
        self._set(assignment_id, status="publish_failed", drop_reason=reason)

    def mark_published(self, assignment_id: str, *, published_id: str) -> None:
        self._set(assignment_id, status="published", published_id=published_id)

    def _set(self, assignment_id: str, **changes) -> None:
        columns = list(changes)
        set_clause = ", ".join(f"{col} = ?" for col in columns)
        values = [changes[col] for col in columns]
        cur = self._conn.execute(
            f"UPDATE assignments SET {set_clause} WHERE id = ?", (*values, assignment_id)
        )
        if cur.rowcount == 0:
            raise KeyError(assignment_id)
        self._conn.commit()


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        mode=row["mode"],
        status=row["status"],
        n_requested=row["n_requested"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


def _row_to_assignment(row: sqlite3.Row) -> Assignment:
    return Assignment(
        id=row["id"],
        run_id=row["run_id"],
        persona_id=row["persona_id"],
        section=row["section"],
        angle=row["angle"] or "",
        status=row["status"],
        created_at=row["created_at"],
        drop_reason=row["drop_reason"],
        final_body=row["final_body"],
        content_hash=row["content_hash"],
        idempotency_key=row["idempotency_key"],
        ledger_digest=row["ledger_digest"],
        published_id=row["published_id"],
    )


def open_runs_store(
    path: str | Path = ":memory:", *, clock: Callable[[], datetime] | None = None
) -> RunStore:
    """Open the brain DB at ``path`` and return a runs/assignments store over it."""
    return RunStore(open_db(path), clock=clock)
