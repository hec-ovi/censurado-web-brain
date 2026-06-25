"""The house-style store: immutable versions plus an active pointer.

The first version auto-activates; later versions stay inactive until promoted; promote
is also rollback; the JSON-shaped fields round-trip; ``list_versions`` is newest-first
with the active one marked; and the active-pointer foreign key holds at the SQL level.
Drives the public API or real SQL only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from newsroom.db import open_db
from newsroom.editorial import StyleGuide, StyleStore


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


@pytest.fixture
def store() -> StyleStore:
    return StyleStore(open_db(":memory:"), clock=_Clock())


def _guide(**over) -> StyleGuide:
    base = dict(
        voice="Sober, plain, attribute every claim.",
        exemplars=[
            {"label": "good", "text": "El Senado rechazo la ley 51 a 49.", "why": "factual, attributed"},
            {"label": "bad", "text": "En un golpe demoledor, el Senado aplasto la ley.", "why": "sensational"},
        ],
        rules=[
            {"id": "attribute", "text": "Atribui cada afirmacion a una fuente.", "severity": "gate", "scope": "both", "check": "llm"},
            {"id": "min-sources", "text": "Apoya el hecho central en dos fuentes.", "severity": "gate", "scope": "both", "check": "code"},
        ],
        lexicon={"banned_terms": ["demoledor", "escandaloso"], "preferred_swaps": {"polemico": "discutido"}},
        sourcing={"min_sources": 2, "require_attribution": True, "no_fabricated_quotes": True},
        structure={"headline": "<= 5 palabras", "dateline": "CIUDAD, fecha", "lede": "que/quien/cuando"},
    )
    base.update(over)
    return StyleGuide(**base)


# ----- versioning + active pointer -----


def test_first_version_auto_activates(store: StyleStore):
    v1 = store.add_version(_guide(), created_by="hector")
    assert v1.version == 1
    assert v1.is_active is True
    assert store.active_version() == 1
    assert store.active().created_by == "hector"


def test_second_version_is_inactive_until_promoted(store: StyleStore):
    store.add_version(_guide(voice="v1 voice"))
    v2 = store.add_version(_guide(voice="v2 voice"))
    assert v2.version == 2
    assert v2.is_active is False
    assert store.active().voice == "v1 voice"  # still v1

    promoted = store.promote(2)
    assert promoted.version == 2 and promoted.is_active is True
    assert store.active().voice == "v2 voice"


def test_promote_is_rollback(store: StyleStore):
    store.add_version(_guide(voice="v1"))
    store.add_version(_guide(voice="v2"), activate=True)
    assert store.active().voice == "v2"
    # Roll back to the earlier version by promoting it; v2 is still retrievable.
    store.promote(1)
    assert store.active().voice == "v1"
    assert store.get(2).voice == "v2"


def test_add_version_with_activate_flag(store: StyleStore):
    store.add_version(_guide(voice="v1"))
    store.add_version(_guide(voice="v2"), activate=True)
    assert store.active_version() == 2


def test_promote_missing_version_raises_key_error(store: StyleStore):
    store.add_version(_guide())
    with pytest.raises(KeyError):
        store.promote(99)


def test_active_is_none_before_any_version(store: StyleStore):
    assert store.active() is None
    assert store.active_version() is None


# ----- round-trip -----


def test_json_fields_round_trip(store: StyleStore):
    v1 = store.add_version(_guide())
    got = store.get(v1.version)
    assert got is not None
    assert got.exemplars[1]["label"] == "bad"
    assert got.rules[0]["id"] == "attribute"
    assert got.lexicon["banned_terms"] == ["demoledor", "escandaloso"]
    assert got.lexicon["preferred_swaps"] == {"polemico": "discutido"}
    assert got.sourcing["min_sources"] == 2
    assert got.structure["headline"] == "<= 5 palabras"


def test_empty_guide_round_trips_to_empty_collections(store: StyleStore):
    v1 = store.add_version(StyleGuide())
    got = store.get(v1.version)
    assert got is not None
    assert got.exemplars == [] and got.rules == []
    assert got.lexicon == {} and got.sourcing == {} and got.structure == {}


def test_list_versions_newest_first_marks_active(store: StyleStore):
    store.add_version(_guide(voice="v1"))
    store.add_version(_guide(voice="v2"))
    store.promote(2)
    versions = store.list_versions()
    assert [g.version for g in versions] == [2, 1]
    assert versions[0].is_active is True and versions[1].is_active is False


def test_persists_across_connections(tmp_path):
    db_file = tmp_path / "sub" / "brain.db"
    StyleStore(open_db(db_file, check_same_thread=False)).add_version(_guide(voice="durable"))
    reopened = StyleStore(open_db(db_file, check_same_thread=False))
    assert reopened.active().voice == "durable"


# ----- SQL-level guarantees -----


def test_active_pointer_foreign_key_enforced():
    conn = open_db(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO style_guide_active (id, version) VALUES (1, 999)")


def test_active_pointer_single_row_check_enforced():
    conn = open_db(":memory:")
    conn.execute(
        "INSERT INTO style_guide (version, created_at) VALUES (1, 't')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO style_guide_active (id, version) VALUES (2, 1)")
