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


# ----- raw schema guarantees -----


def test_core_tables_exist_and_run_tables_are_gone():
    conn = open_db(":memory:")
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"personas", "portals", "location", "style_guide", "prompt_template"} <= names
    # The LLM-newsroom run/coverage records are gone with the inference lane.
    assert {"runs", "assignments", "coverage"}.isdisjoint(names)


def test_beat_check_constraint_enforced_at_sql_level():
    conn = open_db(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO personas (id, display_name, beat, who_i_am, style, created_at, updated_at) "
            "VALUES ('x','X','sports','w','s','t','t')"
        )
