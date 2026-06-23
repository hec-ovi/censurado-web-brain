"""The brain-owned SQLite database: connection + schema, in-process only.

Nothing outside the harness reads this file. It holds the tables from the
architecture doc (B.5): ``personas`` (author identities), ``runs`` (one row per
harness invocation), ``assignments`` (one row per article a run attempts, the
idempotency anchor), and ``coverage`` (one row per published article, the manager's
"coverage memory" for freshness and non-repetition). Step 2 ships the full schema
and the typed persona CRUD on top of it (``newsroom.personas.store``); the ``runs``
/ ``assignments`` CRUD lands with the steps that own those rows (the manager at
Step 6, finalize/publish at Steps 5/7), and ``coverage`` is written by publish
(Step 7) and read by the manager's triage (Step 6). Defining the whole schema now
keeps it in one place and avoids a later migration.

``beat`` on a persona and ``section`` on an assignment share the harness section
vocabulary (``contracts.sections``); the CHECK on ``beat`` mirrors that enum so the
constraint and the Python validation cannot drift apart.

``idempotency_key`` is NULL until an assignment is finalized, then it is the
content-derived key (B.5). The unique index allows many NULLs (SQLite treats NULLs
as distinct) but rejects two assignments that finalize to the same key.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["SCHEMA", "open_db"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS personas (
  id            TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  beat          TEXT NOT NULL CHECK (beat IN ('tech','world','politics','economics')),
  who_i_am      TEXT NOT NULL,
  about         TEXT,
  style         TEXT NOT NULL,
  few_shots_pos TEXT,
  few_shots_neg TEXT,
  sources       TEXT,
  avatar_path   TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id          TEXT PRIMARY KEY,
  mode        TEXT NOT NULL CHECK (mode IN ('manual','express','managed')),
  status      TEXT NOT NULL,
  n_requested INTEGER,
  created_at  TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
  id              TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES runs(id),
  persona_id      TEXT NOT NULL REFERENCES personas(id),
  section         TEXT NOT NULL,
  angle           TEXT,
  status          TEXT NOT NULL,
  drop_reason     TEXT,
  final_body      TEXT,
  content_hash    TEXT,
  idempotency_key TEXT,
  ledger_digest   TEXT,
  published_id    TEXT,
  created_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_assignment_idem
  ON assignments(idempotency_key);

CREATE TABLE IF NOT EXISTS coverage (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  assignment_id TEXT REFERENCES assignments(id),
  published_id  TEXT,
  slug          TEXT,
  section       TEXT NOT NULL,
  headline      TEXT NOT NULL,
  topics        TEXT,                         -- JSON array
  entities      TEXT,                         -- JSON array
  fingerprint   TEXT NOT NULL,                -- JSON array of token signatures (tokens may contain spaces)
  content_hash  TEXT,
  published_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coverage_published_at ON coverage(published_at);
"""


def open_db(path: str | Path = ":memory:", *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the brain database, applying the schema (idempotent) and pragmas.

    ``path`` defaults to an in-memory database (tests). A file path's parent
    directory is created if missing. Foreign keys are enforced (off by default in
    SQLite) so the ``assignments`` references are real; rows come back as
    ``sqlite3.Row`` for name-based access.

    ``check_same_thread`` defaults to True (sqlite's own guard). The brain's HTTP
    layer opens with it False and serializes access under a lock, so one shared
    connection (one in-memory DB) can be reached from a request handler and a
    background synthesis thread.
    """
    is_memory = str(path) == ":memory:"
    if not is_memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
