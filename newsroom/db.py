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
  language      TEXT NOT NULL DEFAULT 'español neutro',
  few_shots_pos TEXT,
  few_shots_neg TEXT,
  sources       TEXT,
  avatar_path   TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id          TEXT PRIMARY KEY,
  mode        TEXT NOT NULL,                -- validated in code (RunStore._RUN_MODES); no CHECK,
                                            -- so a new mode (e.g. 'direct') is a free addition,
                                            -- like assignments.status
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
  entities        TEXT,                         -- JSON array of story entities (de-dup signature)
  status          TEXT NOT NULL,
  drop_reason     TEXT,
  final_body      TEXT,
  content_hash    TEXT,
  idempotency_key TEXT,
  ledger_digest   TEXT,
  published_id    TEXT,
  image_url       TEXT,
  image_prompt    TEXT,
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

-- ----- Operator-editable editorial config (the console's settings panel). -----
-- These three carry what an operator tunes at runtime without a redeploy: WHERE the
-- news comes from (location + the portal allowlist) and HOW it is written (the house
-- style guide). They are read by the manager/research seams and the journalist
-- pipeline, and edited over the brain HTTP API. config.Settings still provides the
-- boot SEED; the live, editable copy lives here so a console edit takes effect without
-- restarting the process.

-- Single-row publication location (id is pinned to 1). Drives place-scoped discovery
-- and the manager's "you are the editor for <place>" framing.
CREATE TABLE IF NOT EXISTS location (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  region        TEXT NOT NULL,                 -- ISO-3166-1 alpha-2 (e.g. AR); Google News gl
  ui_lang       TEXT NOT NULL,                 -- BCP47 (e.g. es-419); Google News hl
  language      TEXT NOT NULL,                 -- ISO-639-1 (e.g. es); websearch language scope
  gdelt_country TEXT NOT NULL DEFAULT '',      -- FIPS-10-4 (e.g. AR); optional GDELT feed
  city          TEXT NOT NULL DEFAULT '',
  latlong       TEXT NOT NULL DEFAULT '',
  updated_at    TEXT NOT NULL
);

-- The operator's own local news portals (clarin.com, lanacion.com, ...). Discovery
-- pulls these directly (native RSS / news sitemaps) and scopes web_search to them.
CREATE TABLE IF NOT EXISTS portals (
  id              TEXT PRIMARY KEY,            -- slug of the domain (clarin-com)
  domain          TEXT NOT NULL UNIQUE,        -- clarin.com
  homepage        TEXT NOT NULL DEFAULT '',
  feed_urls       TEXT NOT NULL DEFAULT '[]',  -- JSON array of known feed URLs
  feed_type       TEXT NOT NULL DEFAULT 'auto'
                    CHECK (feed_type IN ('auto','native_rss','atom','news_sitemap','site_search')),
  language        TEXT NOT NULL DEFAULT 'es',
  ownership_group TEXT NOT NULL DEFAULT '',    -- independence grouping for corroboration counting
  enabled         INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL DEFAULT 'unknown',   -- unknown|ok|unreachable (health-checker)
  last_checked    TEXT NOT NULL DEFAULT '',
  last_ok         TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

-- The house style guide ("the model of redaction + rules"), as IMMUTABLE versions
-- plus a one-row active pointer. An edit inserts a new version; promote/rollback just
-- moves the pointer, so every past version is auditable and instantly restorable.
CREATE TABLE IF NOT EXISTS style_guide (
  version    INTEGER PRIMARY KEY AUTOINCREMENT,
  voice      TEXT NOT NULL DEFAULT '',         -- holistic house voice (prose; NO length phrasing)
  exemplars  TEXT NOT NULL DEFAULT '[]',       -- JSON array of {label: good|bad, text, why}
  rules      TEXT NOT NULL DEFAULT '[]',       -- JSON array of {id, text, severity, scope, check}
  lexicon    TEXT NOT NULL DEFAULT '{}',       -- JSON object {banned_terms, preferred_swaps}
  sourcing   TEXT NOT NULL DEFAULT '{}',       -- JSON object {min_sources, require_attribution, ...}
  structure  TEXT NOT NULL DEFAULT '{}',       -- JSON object {headline, dateline, lede}
  created_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS style_guide_active (
  id      INTEGER PRIMARY KEY CHECK (id = 1),
  version INTEGER NOT NULL REFERENCES style_guide(version)
);
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

    A FILE database also gets WAL journaling and a busy timeout. The automation
    entry point (``newsroom.cli``) runs as a SEPARATE process from a live brain, so
    two processes may touch the same file: WAL lets a reader and a writer proceed
    concurrently, and the busy timeout waits out a brief writer lock instead of
    failing immediately with "database is locked". Both have no effect on an in-memory
    database, so they are applied only to a file.
    """
    is_memory = str(path) == ":memory:"
    if not is_memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not is_memory:
        # Multi-process safety for the file-backed deployment (see the docstring).
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


# Columns added to existing tables after the initial schema shipped. CREATE TABLE IF
# NOT EXISTS leaves an existing table untouched, so a deployed brain DB needs these
# added explicitly. Each is idempotent (skipped when already present); a fresh DB
# already has them from SCHEMA, so this is a no-op there.
_ADDED_COLUMNS = {
    "assignments": (
        ("image_url", "TEXT"),
        ("image_prompt", "TEXT"),
        # The entity de-dup channel shipped after the initial assignments table; a live
        # brain DB predates it, so it is added explicitly (NULL on old rows -> []).
        ("entities", "TEXT"),
    ),
    # The persona writer language shipped after the initial personas table. A live
    # personas.db predates it, so the column is added explicitly with the Spanish
    # default; existing rows inherit it. A fresh DB already has it from SCHEMA.
    "personas": (
        ("language", "TEXT NOT NULL DEFAULT 'español neutro'"),
    ),
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    _migrate_runs_mode_check(conn)


def _migrate_runs_mode_check(conn: sqlite3.Connection) -> None:
    """Drop the legacy 3-mode CHECK on ``runs.mode`` so the direct-from-link run
    record (``mode='direct'``) is accepted.

    A DB created before that change constrained ``mode`` to ('manual','express',
    'managed'); ``CREATE TABLE IF NOT EXISTS`` never recreates an existing table, so the
    old CHECK lingers and rejects a new mode. Recreate the table WITHOUT the CHECK
    (mode is validated in code, exactly like ``assignments.status`` carries no CHECK).
    A fresh DB already has the no-CHECK schema, so this is a no-op there. Best-effort
    and guarded: it copies before dropping inside one transaction and rolls back on any
    error, so a failure leaves the existing table intact."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'"
    ).fetchone()
    if row is None or "CHECK" not in (row[0] or "").upper():
        return  # fresh schema (no CHECK) or no runs table yet
    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(
            "CREATE TABLE _runs_migrated ("
            " id TEXT PRIMARY KEY, mode TEXT NOT NULL, status TEXT NOT NULL,"
            " n_requested INTEGER, created_at TEXT NOT NULL, finished_at TEXT)"
        )
        conn.execute(
            "INSERT INTO _runs_migrated (id, mode, status, n_requested, created_at, finished_at)"
            " SELECT id, mode, status, n_requested, created_at, finished_at FROM runs"
        )
        conn.execute("DROP TABLE runs")
        conn.execute("ALTER TABLE _runs_migrated RENAME TO runs")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
