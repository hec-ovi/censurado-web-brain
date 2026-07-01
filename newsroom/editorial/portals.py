"""The portal registry: the operator's own local news sources.

A portal is a news site the publication pulls from (e.g. ``example.com``,
``news.example``). Discovery reads enabled portals directly (native RSS / news
sitemaps) and scopes the websearch tool to their domains, so the registry is the
allowlist that makes sourcing place-local and operator-owned. ``ownership_group`` lets
corroboration counting treat co-owned outlets as one independent source.

Typed CRUD mirroring ``newsroom.personas.store``: ``id`` is derived from the domain
(``example.com`` -> ``example-com``) so re-adding the same domain is a clean duplicate
error rather than a second row. ``feed_urls`` round-trips as a Python list; ``enabled``
round-trips as a bool. Timestamps come from an injectable ``clock``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from newsroom.personas.store import slugify

__all__ = ["Portal", "PortalStore", "portal_slug", "normalize_domain"]

FEED_TYPES = ("auto", "native_rss", "atom", "news_sitemap", "site_search")

# Fields a caller may change after creation. ``id`` and the timestamps are managed.
_MUTABLE_FIELDS = (
    "domain",
    "homepage",
    "description",
    "feed_urls",
    "feed_type",
    "language",
    "ownership_group",
    "enabled",
    "status",
    "last_checked",
    "last_ok",
)
_JSON_FIELDS = ("feed_urls",)
_BOOL_FIELDS = ("enabled",)


def normalize_domain(raw: str) -> str:
    """Reduce a URL or host to a bare registrable host: lowercase, no scheme, no
    leading ``www.``, no path or trailing slash. ``https://www.Example.com/x`` ->
    ``example.com``."""
    text = raw.strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)  # drop scheme
    text = text.split("/", 1)[0]  # drop path
    text = text.split("@", 1)[-1]  # drop any userinfo
    if text.startswith("www."):
        text = text[4:]
    return text.strip(".")


def portal_slug(domain: str) -> str:
    """The registry id for a domain: the normalized domain, slugified
    (``example.com`` -> ``example-com``)."""
    return slugify(normalize_domain(domain))


@dataclass
class Portal:
    """One news source. ``domain`` is required; ``id`` is derived from it when blank.
    ``feed_urls`` defaults to empty and is stored as a JSON array."""

    domain: str
    id: str = ""
    homepage: str = ""
    description: str = ""
    feed_urls: list = field(default_factory=list)
    feed_type: str = "auto"
    language: str = "es"
    ownership_group: str = ""
    enabled: bool = True
    status: str = "unknown"
    last_checked: str = ""
    last_ok: str = ""
    created_at: str = ""
    updated_at: str = ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PortalStore:
    """Typed CRUD over ``portals``. Construct with an open brain connection."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None):
        self._conn = conn
        self._clock = clock or _utcnow

    # ----- writes -----

    def create(self, portal: Portal) -> Portal:
        """Insert a portal and return the stored row. Normalizes the domain, derives
        ``id`` from it when blank. Raises ``ValueError`` on a blank/underivable domain,
        an invalid ``feed_type``, or a duplicate id."""
        domain = normalize_domain(portal.domain)
        if not domain:
            raise ValueError("portal domain is empty")
        if portal.feed_type not in FEED_TYPES:
            raise ValueError(f"invalid feed_type {portal.feed_type!r}; must be one of {FEED_TYPES}")
        portal_id = portal.id.strip() or portal_slug(domain)
        if not portal_id:
            raise ValueError(f"could not derive a portal id from domain {portal.domain!r}")

        now = self._clock().isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO portals (
                  id, domain, homepage, description, feed_urls, feed_type, language,
                  ownership_group, enabled, status, last_checked, last_ok, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portal_id,
                    domain,
                    portal.homepage,
                    portal.description,
                    json.dumps(portal.feed_urls),
                    portal.feed_type,
                    portal.language,
                    portal.ownership_group,
                    1 if portal.enabled else 0,
                    portal.status,
                    portal.last_checked,
                    portal.last_ok,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"portal already exists: {portal_id} ({domain})") from exc
        self._conn.commit()
        stored = self.get(portal_id)
        assert stored is not None  # just inserted
        return stored

    def update(self, portal_id: str, **changes) -> Portal:
        """Apply ``changes`` to a portal and bump ``updated_at``. Raises ``KeyError`` if
        missing, ``ValueError`` on an unknown field or invalid ``feed_type``."""
        if self.get(portal_id) is None:
            raise KeyError(portal_id)
        unknown = set(changes) - set(_MUTABLE_FIELDS)
        if unknown:
            raise ValueError(f"cannot update unknown field(s): {sorted(unknown)}")
        if "feed_type" in changes and changes["feed_type"] not in FEED_TYPES:
            raise ValueError(f"invalid feed_type {changes['feed_type']!r}; must be one of {FEED_TYPES}")
        if "domain" in changes:
            changes["domain"] = normalize_domain(changes["domain"])
            if not changes["domain"]:
                raise ValueError("portal domain is empty")

        columns = list(changes)
        values: list = []
        for col in columns:
            value = changes[col]
            if col in _JSON_FIELDS:
                value = json.dumps(value)
            elif col in _BOOL_FIELDS:
                value = 1 if value else 0
            values.append(value)
        columns.append("updated_at")
        values.append(self._clock().isoformat())
        set_clause = ", ".join(f"{col} = ?" for col in columns)
        try:
            self._conn.execute(
                f"UPDATE portals SET {set_clause} WHERE id = ?", (*values, portal_id)
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("another portal already uses that domain") from exc
        self._conn.commit()
        stored = self.get(portal_id)
        assert stored is not None  # existence checked above
        return stored

    def set_enabled(self, portal_id: str, enabled: bool) -> Portal:
        """Convenience toggle used by the panel."""
        return self.update(portal_id, enabled=enabled)

    def delete(self, portal_id: str) -> bool:
        """Delete a portal. Returns True if a row was removed, False if already absent."""
        cur = self._conn.execute("DELETE FROM portals WHERE id = ?", (portal_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ----- reads -----

    def get(self, portal_id: str) -> Portal | None:
        row = self._conn.execute("SELECT * FROM portals WHERE id = ?", (portal_id,)).fetchone()
        return _row_to_portal(row) if row is not None else None

    def get_by_domain(self, domain: str) -> Portal | None:
        return self.get(portal_slug(domain))

    def list(self, *, enabled: bool | None = None) -> list[Portal]:
        """All portals, oldest first, optionally filtered to enabled/disabled."""
        if enabled is None:
            rows = self._conn.execute("SELECT * FROM portals ORDER BY created_at, id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM portals WHERE enabled = ? ORDER BY created_at, id",
                (1 if enabled else 0,),
            ).fetchall()
        return [_row_to_portal(r) for r in rows]

    def domains(self) -> list[str]:
        """The bare domains of all ENABLED portals, handy for scoping a site: search."""
        return [p.domain for p in self.list(enabled=True)]


def _row_to_portal(row: sqlite3.Row) -> Portal:
    return Portal(
        id=row["id"],
        domain=row["domain"],
        homepage=row["homepage"] or "",
        description=row["description"] or "",
        feed_urls=json.loads(row["feed_urls"]) if row["feed_urls"] else [],
        feed_type=row["feed_type"],
        language=row["language"],
        ownership_group=row["ownership_group"] or "",
        enabled=bool(row["enabled"]),
        status=row["status"],
        last_checked=row["last_checked"] or "",
        last_ok=row["last_ok"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
