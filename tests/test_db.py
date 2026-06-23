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


def test_in_memory_db_is_not_put_into_wal(tmp_path):
    conn = open_db(":memory:")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
