"""The persona store round-trips typed personas (incl. NEGATIVE few-shots), bumps
``updated_at`` on edit, enforces the beat enum, and persists to a file. A second
suite drives the raw schema so the ``runs`` / ``assignments`` tables shipped at
Step 2 (CHECK, foreign keys, the unique idempotency index) are real, not stubs.

Every test goes through the store's public API or executes real SQL against the
brain DB; nothing patches internals.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from newsroom.db import open_db
from newsroom.personas import Persona, PersonaStore, open_store


class _Clock:
    """A deterministic clock that advances one second per call, so ``created_at``
    and ``updated_at`` are comparable without racing the wall clock."""

    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


@pytest.fixture
def store() -> PersonaStore:
    return PersonaStore(open_db(":memory:"), clock=_Clock())


def _full_persona(**over) -> Persona:
    base = dict(
        display_name="Mara Vance",
        beat="politics",
        who_i_am="You ARE Mara Vance, a wire-service political reporter.",
        style="Plain declaratives, no adjectives of praise, attribute every claim.",
        about="Covers elections and institutions.",
        few_shots_pos=[
            {"prompt": "the vote", "good": "The Senate rejected the bill 51-49."},
        ],
        few_shots_neg=[
            {"prompt": "the vote", "bad": "In a stunning blow, the Senate crushed the bill!"},
            {"prompt": "the leak", "bad": "Sources say the explosive memo will shock you."},
        ],
        sources=["apnews.com", "reuters.com"],
        avatar_path="avatars/mara.png",
    )
    base.update(over)
    return Persona(**base)


# ----- round-trip through the public API -----


def test_round_trips_full_persona_including_negative_few_shots(store: PersonaStore):
    created = store.create(_full_persona())
    got = store.get(created.id)
    assert got is not None
    assert got.display_name == "Mara Vance"
    assert got.beat == "politics"
    assert got.who_i_am.startswith("You ARE Mara Vance")
    assert got.about == "Covers elections and institutions."
    assert got.sources == ["apnews.com", "reuters.com"]
    assert got.avatar_path == "avatars/mara.png"
    # The negative exemplars survive verbatim, nested structure and all.
    assert got.few_shots_pos == [{"prompt": "the vote", "good": "The Senate rejected the bill 51-49."}]
    assert got.few_shots_neg == [
        {"prompt": "the vote", "bad": "In a stunning blow, the Senate crushed the bill!"},
        {"prompt": "the leak", "bad": "Sources say the explosive memo will shock you."},
    ]


def test_id_is_derived_from_display_name_when_blank(store: PersonaStore):
    created = store.create(_full_persona(display_name="Mara Vance", id=""))
    assert created.id == "mara-vance"


def test_explicit_id_is_honored(store: PersonaStore):
    created = store.create(_full_persona(id="mv-politics"))
    assert created.id == "mv-politics"
    assert store.get("mv-politics") is not None


def test_create_stamps_equal_timestamps(store: PersonaStore):
    created = store.create(_full_persona())
    assert created.created_at == created.updated_at
    assert created.created_at  # non-empty ISO string


def test_empty_list_fields_default_to_empty(store: PersonaStore):
    created = store.create(
        Persona(
            display_name="Min Ono",
            beat="tech",
            who_i_am="You ARE Min Ono.",
            style="Terse.",
        )
    )
    got = store.get(created.id)
    assert got is not None
    assert got.few_shots_pos == []
    assert got.few_shots_neg == []
    assert got.sources == []
    assert got.about == ""
    assert got.avatar_path == ""


# ----- validation -----


def test_invalid_beat_is_rejected_and_nothing_inserted(store: PersonaStore):
    with pytest.raises(ValueError, match="invalid beat"):
        store.create(_full_persona(beat="sports"))
    assert store.list() == []


def test_underivable_id_is_rejected(store: PersonaStore):
    with pytest.raises(ValueError, match="could not be derived"):
        store.create(_full_persona(display_name="!!!", id=""))


def test_duplicate_id_is_rejected(store: PersonaStore):
    store.create(_full_persona(id="dup"))
    with pytest.raises(ValueError, match="already exists"):
        store.create(_full_persona(id="dup", display_name="Someone Else"))


# ----- listing -----


def test_list_returns_all_oldest_first(store: PersonaStore):
    store.create(_full_persona(id="first", display_name="First"))
    store.create(_full_persona(id="second", display_name="Second", beat="tech"))
    ids = [p.id for p in store.list()]
    assert ids == ["first", "second"]


def test_list_filters_by_beat(store: PersonaStore):
    store.create(_full_persona(id="p1", beat="politics"))
    store.create(_full_persona(id="t1", display_name="Tech One", beat="tech"))
    store.create(_full_persona(id="t2", display_name="Tech Two", beat="tech"))
    assert [p.id for p in store.list(beat="tech")] == ["t1", "t2"]
    assert [p.id for p in store.list(beat="politics")] == ["p1"]
    assert store.list(beat="economics") == []


# ----- active flag (the mirror's soft-deactivate) -----


def test_persona_is_active_by_default(store: PersonaStore):
    created = store.create(_full_persona())
    assert created.active is True
    assert store.get(created.id).active is True


def test_list_hides_inactive_by_default_but_include_inactive_shows_it(store: PersonaStore):
    store.create(_full_persona(id="live", display_name="Live One"))
    store.create(_full_persona(id="gone", display_name="Gone One", beat="tech"))
    store.update("gone", active=False)
    # The run path (default list) only sees the active persona...
    assert [p.id for p in store.list()] == ["live"]
    assert [p.id for p in store.list(beat="tech")] == []
    # ...while the registry view (include_inactive) still sees both.
    assert [p.id for p in store.list(include_inactive=True)] == ["live", "gone"]
    assert [p.id for p in store.list(beat="tech", include_inactive=True)] == ["gone"]


def test_active_round_trips_as_bool_and_reactivates(store: PersonaStore):
    created = store.create(_full_persona())
    deactivated = store.update(created.id, active=False)
    assert deactivated.active is False
    assert store.get(created.id).active is False
    reactivated = store.update(created.id, active=True)
    assert reactivated.active is True
    assert store.list()[0].id == created.id  # back in the run path


def test_create_inactive_shell_persona(store: PersonaStore):
    # A web-created author with no local prompt yet is mirrored as an inactive shell.
    created = store.create(_full_persona(id="shell", active=False))
    assert created.active is False
    assert store.list() == []
    assert [p.id for p in store.list(include_inactive=True)] == ["shell"]


# ----- update -----


def test_update_changes_field_and_bumps_updated_at(store: PersonaStore):
    created = store.create(_full_persona())
    updated = store.update(created.id, style="Even terser.")
    assert updated.style == "Even terser."
    assert updated.created_at == created.created_at  # unchanged
    assert updated.updated_at > created.updated_at  # advanced by the clock
    # Untouched fields are preserved.
    assert updated.few_shots_neg == created.few_shots_neg
    assert updated.display_name == created.display_name


def test_update_replaces_negative_few_shots(store: PersonaStore):
    created = store.create(_full_persona())
    new_neg = [{"prompt": "x", "bad": "clickbait headline here"}]
    updated = store.update(created.id, few_shots_neg=new_neg)
    assert updated.few_shots_neg == new_neg
    assert store.get(created.id).few_shots_neg == new_neg


def test_update_can_change_beat(store: PersonaStore):
    created = store.create(_full_persona(beat="politics"))
    updated = store.update(created.id, beat="world")
    assert updated.beat == "world"


def test_update_unknown_field_is_rejected(store: PersonaStore):
    created = store.create(_full_persona())
    with pytest.raises(ValueError, match="unknown field"):
        store.update(created.id, nickname="Mar")


def test_update_invalid_beat_is_rejected(store: PersonaStore):
    created = store.create(_full_persona())
    with pytest.raises(ValueError, match="invalid beat"):
        store.update(created.id, beat="weather")


def test_update_missing_persona_raises_key_error(store: PersonaStore):
    with pytest.raises(KeyError):
        store.update("ghost", style="x")


# ----- delete -----


def test_delete_removes_and_reports_true(store: PersonaStore):
    created = store.create(_full_persona())
    assert store.delete(created.id) is True
    assert store.get(created.id) is None


def test_delete_missing_reports_false(store: PersonaStore):
    assert store.delete("never-existed") is False


def test_delete_referenced_persona_raises_value_error():
    # Share one connection between the store and raw SQL so the assignment's
    # foreign key points at the persona the store just created.
    conn = open_db(":memory:")
    store = PersonaStore(conn, clock=_Clock())
    store.create(_full_persona(id="p"))
    conn.execute("INSERT INTO runs (id, mode, status, created_at) VALUES ('r','managed','running','t')")
    conn.execute(
        "INSERT INTO assignments (id, run_id, persona_id, section, status, created_at) "
        "VALUES ('a','r','p','politics','assigned','t')"
    )
    conn.commit()
    with pytest.raises(ValueError, match="referenced by assignments"):
        store.delete("p")
    assert store.get("p") is not None  # the rejected delete left the row intact


# ----- language -----


def test_language_defaults_to_spanish_when_unset(store: PersonaStore):
    created = store.create(
        Persona(
            display_name="Default Lang",
            beat="tech",
            who_i_am="You ARE Default Lang.",
            style="Terse.",
        )
    )
    got = store.get(created.id)
    assert got is not None
    assert got.language == "español neutro"


def test_explicit_language_round_trips(store: PersonaStore):
    created = store.create(_full_persona(id="english-writer", language="English"))
    got = store.get(created.id)
    assert got is not None
    assert got.language == "English"


def test_update_can_change_language(store: PersonaStore):
    created = store.create(_full_persona())
    assert created.language == "español neutro"
    updated = store.update(created.id, language="português brasileiro")
    assert updated.language == "português brasileiro"
    assert store.get(created.id).language == "português brasileiro"


# ----- file persistence + idempotent schema init -----


def test_persona_persists_across_connections(tmp_path):
    db_file = tmp_path / "sub" / "personas.db"  # parent dir does not exist yet
    s1 = open_store(db_file, clock=_Clock())
    s1.create(_full_persona(id="durable"))

    # Reopening the same file re-applies the schema (idempotent) and reads the row.
    s2 = open_store(db_file)
    got = s2.get("durable")
    assert got is not None
    assert got.few_shots_neg[0]["bad"].startswith("In a stunning blow")


# ----- raw schema guarantees (runs/assignments shipped now, CRUD lands later) -----


def test_all_three_tables_exist():
    conn = open_db(":memory:")
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"personas", "runs", "assignments"} <= names


def test_beat_check_constraint_enforced_at_sql_level():
    conn = open_db(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO personas (id, display_name, beat, who_i_am, style, created_at, updated_at) "
            "VALUES ('x','X','sports','w','s','t','t')"
        )


def test_run_mode_validated_in_code_not_by_a_sql_check():
    # runs.mode carries NO SQL CHECK, so adding a mode (e.g. the direct-from-link
    # 'direct') is a free, non-breaking change, like assignments.status. Validation
    # moved to RunStore.create_run instead.
    from newsroom.runs import RunStore

    conn = open_db(":memory:")
    # The SQL level accepts any mode string (the CHECK is gone) ...
    conn.execute(
        "INSERT INTO runs (id, mode, status, created_at) VALUES ('rraw','whatever','queued','t')"
    )
    # ... but the store validates: a bad mode raises, and 'direct' (run mode 3) is valid.
    store = RunStore(conn)
    with pytest.raises(ValueError, match="invalid run mode"):
        store.create_run(mode="cron")
    assert store.create_run(mode="direct").mode == "direct"


def test_open_db_migrates_a_legacy_runs_mode_check(tmp_path):
    # A DB created before the direct-from-link mode constrained runs.mode to a 3-mode
    # CHECK that rejects 'direct'. open_db must recreate the table WITHOUT the CHECK,
    # preserving existing rows, so mode 3 is accepted on an upgraded DB.
    db = tmp_path / "legacy.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        "CREATE TABLE runs (id TEXT PRIMARY KEY,"
        " mode TEXT NOT NULL CHECK (mode IN ('manual','express','managed')),"
        " status TEXT NOT NULL, n_requested INTEGER, created_at TEXT NOT NULL, finished_at TEXT);"
        "INSERT INTO runs (id, mode, status, created_at) VALUES ('old','managed','done','t');"
    )
    raw.commit()
    raw.close()

    conn = open_db(db, check_same_thread=False)
    # The legacy row survived the recreation ...
    assert conn.execute("SELECT mode FROM runs WHERE id='old'").fetchone()[0] == "managed"
    # ... and 'direct' now inserts (the CHECK is gone, validation lives in code).
    conn.execute("INSERT INTO runs (id, mode, status, created_at) VALUES ('d','direct','running','t')")
    assert conn.execute("SELECT mode FROM runs WHERE id='d'").fetchone()[0] == "direct"


def test_assignment_foreign_keys_enforced():
    conn = open_db(":memory:")
    # run_id / persona_id reference rows that do not exist -> FK violation.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO assignments (id, run_id, persona_id, section, status, created_at) "
            "VALUES ('a1','missing-run','missing-persona','tech','assigned','t')"
        )


def test_idempotency_key_unique_but_many_nulls_allowed():
    conn = open_db(":memory:")
    conn.execute("INSERT INTO runs (id, mode, status, created_at) VALUES ('r','managed','running','t')")
    conn.execute(
        "INSERT INTO personas (id, display_name, beat, who_i_am, style, created_at, updated_at) "
        "VALUES ('p','P','tech','w','s','t','t')"
    )

    def _assignment(aid: str, key) -> None:
        conn.execute(
            "INSERT INTO assignments (id, run_id, persona_id, section, status, idempotency_key, created_at) "
            "VALUES (?, 'r', 'p', 'tech', 'assigned', ?, 't')",
            (aid, key),
        )

    # Two un-finalized assignments both carry NULL keys: allowed.
    _assignment("a1", None)
    _assignment("a2", None)
    # First finalized key is fine; a second assignment with the SAME key collides.
    _assignment("a3", "idem-123")
    with pytest.raises(sqlite3.IntegrityError):
        _assignment("a4", "idem-123")
