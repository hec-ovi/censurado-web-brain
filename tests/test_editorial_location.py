"""The location store: a single editable row that scopes where news comes from.

``get`` always returns a usable value (default Argentina/Spanish until seeded), ``set``
upserts and bumps ``updated_at``, ``seed`` is idempotent and never clobbers edits, and
the single-row CHECK holds at the SQL level. Every test drives the public API or real
SQL; nothing patches internals.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from newsroom.db import open_db
from newsroom.editorial import DEFAULT_LOCATION, Location, LocationStore


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


@pytest.fixture
def store() -> LocationStore:
    return LocationStore(open_db(":memory:"), clock=_Clock())


def test_default_is_argentina_spanish_before_anything_is_stored(store: LocationStore):
    assert store.is_set() is False
    loc = store.get()
    assert (loc.region, loc.language, loc.ui_lang) == ("AR", "es", "es-419")
    assert loc == DEFAULT_LOCATION


def test_google_news_views_are_derived(store: LocationStore):
    loc = store.set(region="AR", ui_lang="es-419")
    assert loc.gl == "AR"
    assert loc.hl == "es-419"
    assert loc.ceid == "AR:es-419"


def test_set_upserts_and_bumps_updated_at(store: LocationStore):
    first = store.set(city="Buenos Aires")
    assert store.is_set() is True
    assert first.city == "Buenos Aires"
    assert first.region == "AR"  # unchanged fields keep their value
    second = store.set(region="ES", language="es")
    assert second.region == "ES"
    assert second.city == "Buenos Aires"  # prior change preserved across upsert
    assert second.updated_at > first.updated_at


def test_set_unknown_field_is_rejected(store: LocationStore):
    with pytest.raises(ValueError, match="unknown field"):
        store.set(country="Argentina")


def test_set_blank_required_field_is_rejected(store: LocationStore):
    with pytest.raises(ValueError, match="region is required"):
        store.set(region="  ")


def test_seed_writes_default_then_is_a_noop(store: LocationStore):
    seeded = store.seed()
    assert seeded.region == "AR"
    stamped = seeded.updated_at
    # A second seed must NOT overwrite or re-stamp: it is a true no-op.
    again = store.seed(Location(region="ES", language="es", ui_lang="es-ES"))
    assert again.region == "AR"
    assert again.updated_at == stamped


def test_seed_does_not_clobber_an_operator_edit(store: LocationStore):
    store.set(region="UY", city="Montevideo")
    store.seed()  # bootstrap re-run
    loc = store.get()
    assert (loc.region, loc.city) == ("UY", "Montevideo")


def test_persists_across_connections(tmp_path):
    db_file = tmp_path / "sub" / "brain.db"
    LocationStore(open_db(db_file, check_same_thread=False)).set(region="CL", language="es")
    reopened = LocationStore(open_db(db_file, check_same_thread=False)).get()
    assert reopened.region == "CL"


def test_single_row_check_enforced_at_sql_level():
    conn = open_db(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO location (id, region, ui_lang, language, updated_at) "
            "VALUES (2, 'AR', 'es-419', 'es', 't')"
        )
