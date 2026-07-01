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
            "INSERT INTO portals (id, domain, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("example-com", "example.com", "2026-06-23T00:00:00Z", "2026-06-23T00:00:00Z"),
        )
        live.commit()
        assert other.execute(
            "SELECT domain FROM portals WHERE id = 'example-com'"
        ).fetchone()[0] == "example.com"
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
            "INSERT INTO portals (id, domain, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("seed", "seed.example", "t", "t"),
        )
        writer.commit()

        # Open and hold a read transaction on `reader` (the shared lock is taken at the
        # first read and held until the transaction ends).
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM portals").fetchone()[0] == 1

        # Commit a new row on `writer` while that read transaction is still open.
        writer.execute(
            "INSERT INTO portals (id, domain, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("r2", "r2.example", "t", "t"),
        )
        writer.commit()  # returns under WAL; would block-then-error under rollback journal
        assert writer.execute("SELECT count(*) FROM portals").fetchone()[0] == 2
    finally:
        reader.rollback()
        reader.close()
        writer.close()


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


def test_open_db_migrates_profile_topics_column_onto_an_existing_personas_db(tmp_path):
    # Curated public-profile topics shipped after the personas table. A live DB predates
    # them; the column only appears via the explicit ALTER in _migrate. Build a
    # pre-profile_topics personas table with a row, reopen via open_db, and assert the
    # column was added defaulting to an empty JSON array (so the store reads it back as []).
    import sqlite3

    path = tmp_path / "old_personas.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        """
        CREATE TABLE personas (
          id TEXT PRIMARY KEY, display_name TEXT NOT NULL, beat TEXT NOT NULL,
          who_i_am TEXT NOT NULL, about TEXT, style TEXT NOT NULL,
          language TEXT NOT NULL DEFAULT 'español neutro',
          active INTEGER NOT NULL DEFAULT 1,
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
        assert "profile_topics" in cols
        row = conn.execute("SELECT profile_topics FROM personas WHERE id = 'p1'").fetchone()
        assert row["profile_topics"] == "[]"
    finally:
        conn.close()

    # The store reads the migrated row back with an empty curated list, never NULL.
    from newsroom.personas import PersonaStore

    persona = PersonaStore(open_db(path, check_same_thread=False)).get("p1")
    assert persona is not None and persona.profile_topics == []


def test_open_db_migrates_description_column_onto_an_existing_portals_db(tmp_path):
    # The live brain DB predates the portals.description column. CREATE TABLE IF NOT
    # EXISTS leaves the deployed table untouched, so the column only appears via the
    # explicit ALTER in _migrate. Build a pre-description portals table with a row,
    # reopen via open_db, and assert the column was added defaulting to '' on the
    # existing row (so a PortalStore reads it back as the empty string, never NULL).
    import sqlite3

    from newsroom.editorial import PortalStore

    path = tmp_path / "old_portals.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(
        """
        CREATE TABLE portals (
          id TEXT PRIMARY KEY, domain TEXT NOT NULL UNIQUE,
          homepage TEXT NOT NULL DEFAULT '', feed_urls TEXT NOT NULL DEFAULT '[]',
          feed_type TEXT NOT NULL DEFAULT 'auto', language TEXT NOT NULL DEFAULT 'es',
          ownership_group TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'unknown', last_checked TEXT NOT NULL DEFAULT '',
          last_ok TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO portals (id, domain, created_at, updated_at) "
        "VALUES ('example-com','example.com','t','t')"
    )
    raw.commit()
    raw.close()

    conn = open_db(path, check_same_thread=False)
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(portals)")}
        assert "description" in cols
        # The store reads the migrated row, and the new column is "" rather than NULL.
        portal = PortalStore(conn).get("example-com")
        assert portal is not None and portal.description == ""
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


def test_persona_beat_check_is_single_sourced_from_the_section_enum():
    # The SQL CHECK is built from contracts.sections.SECTION_ENUM, not a hand-kept
    # literal, so the constraint and the Python validation cannot drift apart.
    from newsroom.contracts.sections import SECTION_ENUM
    from newsroom.db import SCHEMA

    expected = ",".join(f"'{section}'" for section in SECTION_ENUM)
    assert f"CHECK (beat IN ({expected}))" in SCHEMA
    assert "__BEAT_VALUES__" not in SCHEMA  # the sentinel was filled in


def test_db_rejects_a_beat_outside_the_section_enum():
    # The schema CHECK (single-sourced from the enum) rejects an unknown beat even on a
    # raw insert that bypasses the store's Python validation.
    import sqlite3

    import pytest

    conn = open_db(":memory:")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO personas (id, display_name, beat, who_i_am, style, created_at, updated_at) "
                "VALUES ('x','X','not-a-section','w','s','t','t')"
            )
    finally:
        conn.close()
