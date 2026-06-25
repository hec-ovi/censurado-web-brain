"""The portal registry store: the operator's own local news sources.

Domains normalize and derive a stable id, ``feed_urls`` and ``enabled`` round-trip,
the feed-type enum and domain uniqueness hold (at the API and at the SQL level), and
listing/filtering behaves. Drives the public API or real SQL only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from newsroom.db import open_db
from newsroom.editorial import Portal, PortalStore, normalize_domain, portal_slug


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


@pytest.fixture
def store() -> PortalStore:
    return PortalStore(open_db(":memory:"), clock=_Clock())


# ----- normalization helpers -----


@pytest.mark.parametrize(
    "raw,domain",
    [
        ("https://www.clarin.com/", "clarin.com"),
        ("http://Clarin.com/politica/x", "clarin.com"),
        ("www.lanacion.com.ar", "lanacion.com.ar"),
        ("infobae.com", "infobae.com"),
    ],
)
def test_normalize_domain(raw, domain):
    assert normalize_domain(raw) == domain


def test_portal_slug_is_domain_slugified():
    assert portal_slug("https://www.clarin.com") == "clarin-com"


# ----- create / round-trip -----


def test_create_normalizes_domain_and_derives_id(store: PortalStore):
    created = store.create(Portal(domain="https://www.clarin.com/", feed_urls=["https://clarin.com/rss"]))
    assert created.id == "clarin-com"
    assert created.domain == "clarin.com"
    assert created.feed_urls == ["https://clarin.com/rss"]
    assert created.enabled is True
    assert created.created_at == created.updated_at


def test_explicit_id_is_honored(store: PortalStore):
    created = store.create(Portal(domain="infobae.com", id="infobae"))
    assert created.id == "infobae"
    assert store.get("infobae") is not None


def test_enabled_round_trips_as_bool(store: PortalStore):
    created = store.create(Portal(domain="lanacion.com", enabled=False))
    got = store.get(created.id)
    assert got is not None and got.enabled is False


def test_invalid_feed_type_is_rejected_and_nothing_inserted(store: PortalStore):
    with pytest.raises(ValueError, match="invalid feed_type"):
        store.create(Portal(domain="clarin.com", feed_type="telepathy"))
    assert store.list() == []


def test_blank_domain_is_rejected(store: PortalStore):
    with pytest.raises(ValueError, match="domain is empty"):
        store.create(Portal(domain="   "))


def test_duplicate_domain_is_rejected(store: PortalStore):
    store.create(Portal(domain="clarin.com"))
    with pytest.raises(ValueError, match="already exists"):
        store.create(Portal(domain="https://www.clarin.com/otra"))  # same registrable host


# ----- update / delete -----


def test_update_changes_field_and_bumps_updated_at(store: PortalStore):
    created = store.create(Portal(domain="clarin.com"))
    updated = store.update(created.id, ownership_group="grupo-clarin", feed_type="native_rss")
    assert updated.ownership_group == "grupo-clarin"
    assert updated.feed_type == "native_rss"
    assert updated.updated_at > created.updated_at


def test_set_enabled_toggles(store: PortalStore):
    created = store.create(Portal(domain="clarin.com"))
    assert store.set_enabled(created.id, False).enabled is False
    assert store.set_enabled(created.id, True).enabled is True


def test_update_unknown_field_is_rejected(store: PortalStore):
    created = store.create(Portal(domain="clarin.com"))
    with pytest.raises(ValueError, match="unknown field"):
        store.update(created.id, paywall=True)


def test_update_missing_portal_raises_key_error(store: PortalStore):
    with pytest.raises(KeyError):
        store.update("ghost", enabled=False)


def test_delete_removes_and_reports(store: PortalStore):
    created = store.create(Portal(domain="clarin.com"))
    assert store.delete(created.id) is True
    assert store.get(created.id) is None
    assert store.delete(created.id) is False


# ----- listing -----


def test_list_oldest_first_and_filter_enabled(store: PortalStore):
    store.create(Portal(domain="clarin.com"))
    store.create(Portal(domain="lanacion.com", enabled=False))
    store.create(Portal(domain="infobae.com"))
    assert [p.id for p in store.list()] == ["clarin-com", "lanacion-com", "infobae-com"]
    assert [p.id for p in store.list(enabled=True)] == ["clarin-com", "infobae-com"]
    assert [p.id for p in store.list(enabled=False)] == ["lanacion-com"]


def test_domains_returns_only_enabled(store: PortalStore):
    store.create(Portal(domain="clarin.com"))
    store.create(Portal(domain="lanacion.com", enabled=False))
    assert store.domains() == ["clarin.com"]


def test_get_by_domain(store: PortalStore):
    store.create(Portal(domain="infobae.com"))
    assert store.get_by_domain("https://www.infobae.com/").id == "infobae-com"


# ----- SQL-level guarantees -----


def test_feed_type_check_enforced_at_sql_level():
    conn = open_db(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO portals (id, domain, feed_type, created_at, updated_at) "
            "VALUES ('x', 'x.com', 'telepathy', 't', 't')"
        )


def test_domain_unique_enforced_at_sql_level():
    conn = open_db(":memory:")
    conn.execute(
        "INSERT INTO portals (id, domain, created_at, updated_at) VALUES ('a', 'clarin.com', 't', 't')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO portals (id, domain, created_at, updated_at) VALUES ('b', 'clarin.com', 't', 't')"
        )
