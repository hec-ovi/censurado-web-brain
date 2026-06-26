"""``open_db`` pragmas (Step 10): a FILE database gets WAL journaling and a busy
timeout so the automation command (a separate process from a live brain) can share
the file without an immediate "database is locked" error; an in-memory database
(the test default) is left in its default mode, where WAL would be meaningless.
"""

from __future__ import annotations

from newsroom.db import open_db


def test_file_db_enables_wal_and_a_busy_timeout(tmp_path):
    conn = open_db(tmp_path / "brain.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # still enforced
    finally:
        conn.close()


def test_two_connections_share_one_file(tmp_path):
    # The deployment shape: a live brain holds one connection while the one-shot
    # automation process opens its own. Both see the schema and a committed write.
    path = tmp_path / "brain.db"
    live = open_db(path, check_same_thread=False)
    other = open_db(path, check_same_thread=False)
    try:
        live.execute(
            "INSERT INTO runs (id, mode, status, created_at) VALUES (?, ?, ?, ?)",
            ("r1", "managed", "running", "2026-06-23T00:00:00Z"),
        )
        live.commit()
        assert other.execute("SELECT status FROM runs WHERE id = 'r1'").fetchone()[0] == "running"
    finally:
        live.close()
        other.close()


def test_a_writer_commits_while_a_reader_holds_an_open_transaction(tmp_path):
    # The WAL-specific guarantee the multi-process deployment leans on: a writer can
    # COMMIT while another connection holds an OPEN read transaction. Under the default
    # rollback journal the writer's commit needs an exclusive lock and would block on
    # the reader's shared lock until the busy timeout, then raise "database is locked";
    # WAL writes to the log without blocking the reader, so the commit succeeds at once.
    path = tmp_path / "brain.db"
    reader = open_db(path, check_same_thread=False)
    writer = open_db(path, check_same_thread=False)
    try:
        writer.execute(
            "INSERT INTO runs (id, mode, status, created_at) VALUES (?, ?, ?, ?)",
            ("seed", "managed", "running", "t"),
        )
        writer.commit()

        # Open and hold a read transaction on `reader` (the shared lock is taken at the
        # first read and held until the transaction ends).
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM runs").fetchone()[0] == 1

        # Commit a new row on `writer` while that read transaction is still open.
        writer.execute(
            "INSERT INTO runs (id, mode, status, created_at) VALUES (?, ?, ?, ?)",
            ("r2", "managed", "running", "t"),
        )
        writer.commit()  # returns under WAL; would block-then-error under rollback journal
        assert writer.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
    finally:
        reader.rollback()
        reader.close()
        writer.close()


def test_open_db_migrates_image_columns_onto_an_existing_db(tmp_path):
    # The upgrade-safety path: a brain DB created before the image columns existed must
    # gain them. CREATE TABLE IF NOT EXISTS leaves a deployed table untouched, so the
    # columns only appear via the explicit ALTER in _migrate. Build a pre-image table,
    # reopen via open_db, and assert the columns were added (and an old row survives).
    import sqlite3

    path = tmp_path / "old.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        """
        CREATE TABLE assignments (
          id TEXT PRIMARY KEY, run_id TEXT, persona_id TEXT, section TEXT, angle TEXT,
          status TEXT, drop_reason TEXT, final_body TEXT, content_hash TEXT,
          idempotency_key TEXT, ledger_digest TEXT, published_id TEXT, created_at TEXT
        );
        """
    )
    raw.execute(
        "INSERT INTO assignments (id, run_id, persona_id, section, status, created_at) "
        "VALUES ('a1','r','p','tech','ready','t')"
    )
    raw.commit()
    raw.close()

    conn = open_db(path, check_same_thread=False)
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(assignments)")}
        assert "image_url" in cols and "image_prompt" in cols
        row = conn.execute(
            "SELECT image_url, image_prompt FROM assignments WHERE id = 'a1'"
        ).fetchone()
        assert row["image_url"] is None and row["image_prompt"] is None  # old row survives, new cols NULL
    finally:
        conn.close()

    # Idempotent: a second open does not error (the columns are already present).
    again = open_db(path, check_same_thread=False)
    again.close()


def test_open_db_migrates_language_column_onto_an_existing_personas_db(tmp_path):
    # The live personas.db predates the writer language column. CREATE TABLE IF NOT
    # EXISTS leaves the deployed table untouched, so the column only appears via the
    # explicit ALTER in _migrate. Build a pre-language personas table with a row,
    # reopen via open_db, and assert the column was added with the Spanish default on
    # the existing row.
    import sqlite3

    path = tmp_path / "old_personas.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        """
        CREATE TABLE personas (
          id TEXT PRIMARY KEY, display_name TEXT NOT NULL, beat TEXT NOT NULL,
          who_i_am TEXT NOT NULL, about TEXT, style TEXT NOT NULL,
          few_shots_pos TEXT, few_shots_neg TEXT, sources TEXT, avatar_path TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO personas (id, display_name, beat, who_i_am, style, created_at, updated_at) "
        "VALUES ('p1','P One','tech','w','s','t','t')"
    )
    raw.commit()
    raw.close()

    conn = open_db(path, check_same_thread=False)
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(personas)")}
        assert "language" in cols
        # The existing row inherits the Spanish default rather than NULL.
        row = conn.execute("SELECT language FROM personas WHERE id = 'p1'").fetchone()
        assert row["language"] == "español neutro"
    finally:
        conn.close()

    # Idempotent: a second open does not error (the column is already present).
    again = open_db(path, check_same_thread=False)
    again.close()


def test_open_db_migrates_active_column_onto_an_existing_personas_db(tmp_path):
    # The mirror's soft-deactivate flag shipped after the personas table. A live DB
    # predates it; the column only appears via the explicit ALTER in _migrate. Build a
    # pre-active personas table with a row, reopen via open_db, and assert the column
    # was added defaulting to active (1) so an existing newsroom keeps writing.
    import sqlite3

    path = tmp_path / "old_personas.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        """
        CREATE TABLE personas (
          id TEXT PRIMARY KEY, display_name TEXT NOT NULL, beat TEXT NOT NULL,
          who_i_am TEXT NOT NULL, about TEXT, style TEXT NOT NULL,
          language TEXT NOT NULL DEFAULT 'español neutro',
          few_shots_pos TEXT, few_shots_neg TEXT, sources TEXT, avatar_path TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO personas (id, display_name, beat, who_i_am, style, created_at, updated_at) "
        "VALUES ('p1','P One','tech','w','s','t','t')"
    )
    raw.commit()
    raw.close()

    conn = open_db(path, check_same_thread=False)
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(personas)")}
        assert "active" in cols
        # The existing row defaults to active rather than NULL/0.
        row = conn.execute("SELECT active FROM personas WHERE id = 'p1'").fetchone()
        assert row["active"] == 1
    finally:
        conn.close()

    # Idempotent: a second open does not error (the column is already present).
    again = open_db(path, check_same_thread=False)
    again.close()


def test_in_memory_db_is_not_put_into_wal(tmp_path):
    conn = open_db(":memory:")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
