"""The publication location: one editable row that scopes where news comes from.

A single logical row (``id`` pinned to 1) holds the place the publication covers.
``region`` is an ISO-3166-1 alpha-2 code (the Google News ``gl`` parameter), ``ui_lang``
is a BCP47 tag (Google News ``hl``), ``language`` is the ISO-639-1 code the websearch
tool scopes to, and ``gdelt_country`` is the FIPS-10-4 code an optional GDELT feed wants
(it differs from the ISO code for some countries, e.g. Spain is ``ES`` in ISO but ``SP``
in FIPS, so it is stored, not derived). The default place is Argentina in Spanish.

``get`` always returns a usable ``Location`` (the stored row, or the in-memory default
when nothing is seeded yet), so callers never branch on "is it configured". ``set`` is
an upsert used by the console; ``seed`` writes the default only when absent, so a
re-run of bootstrap never clobbers an operator's edits.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

__all__ = ["Location", "LocationStore", "DEFAULT_LOCATION"]

# Fields an operator may change. ``updated_at`` is managed by the store.
_MUTABLE_FIELDS = ("region", "ui_lang", "language", "gdelt_country", "city", "latlong")


@dataclass(frozen=True)
class Location:
    """The publication place. Immutable value object; ``set`` returns a new one.

    Defaults describe Argentina in neutral Spanish. ``gl`` / ``hl`` / ``ceid`` are the
    Google-News-shaped views callers actually pass to a feed."""

    region: str = "AR"
    ui_lang: str = "es-419"
    language: str = "es"
    gdelt_country: str = "AR"
    city: str = ""
    latlong: str = ""
    updated_at: str = ""

    @property
    def gl(self) -> str:
        return self.region

    @property
    def hl(self) -> str:
        return self.ui_lang

    @property
    def ceid(self) -> str:
        return f"{self.region}:{self.ui_lang}"


DEFAULT_LOCATION = Location()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocationStore:
    """Read/write the single ``location`` row. Construct with an open brain
    connection (``newsroom.db.open_db``)."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None):
        self._conn = conn
        self._clock = clock or _utcnow

    # ----- reads -----

    def is_set(self) -> bool:
        """True once a row has been persisted (by ``set`` or ``seed``)."""
        row = self._conn.execute("SELECT 1 FROM location WHERE id = 1").fetchone()
        return row is not None

    def get(self) -> Location:
        """The configured location, or the in-memory default when none is stored yet."""
        row = self._conn.execute("SELECT * FROM location WHERE id = 1").fetchone()
        if row is None:
            return DEFAULT_LOCATION
        return Location(
            region=row["region"],
            ui_lang=row["ui_lang"],
            language=row["language"],
            gdelt_country=row["gdelt_country"],
            city=row["city"],
            latlong=row["latlong"],
            updated_at=row["updated_at"],
        )

    # ----- writes -----

    def set(self, **changes) -> Location:
        """Upsert the location row, applying ``changes`` over the current value and
        bumping ``updated_at``. Raises ``ValueError`` on an unknown field or a blank
        ``region`` / ``ui_lang`` / ``language``."""
        unknown = set(changes) - set(_MUTABLE_FIELDS)
        if unknown:
            raise ValueError(f"cannot set unknown field(s): {sorted(unknown)}")

        merged = replace(self.get(), **changes)
        for required in ("region", "ui_lang", "language"):
            if not str(getattr(merged, required)).strip():
                raise ValueError(f"{required} is required and cannot be blank")

        now = self._clock().isoformat()
        self._conn.execute(
            """
            INSERT INTO location (id, region, ui_lang, language, gdelt_country, city, latlong, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              region=excluded.region, ui_lang=excluded.ui_lang, language=excluded.language,
              gdelt_country=excluded.gdelt_country, city=excluded.city, latlong=excluded.latlong,
              updated_at=excluded.updated_at
            """,
            (
                merged.region,
                merged.ui_lang,
                merged.language,
                merged.gdelt_country,
                merged.city,
                merged.latlong,
                now,
            ),
        )
        self._conn.commit()
        return self.get()

    def seed(self, location: Location = DEFAULT_LOCATION) -> Location:
        """Write ``location`` only if none is stored yet, then return the current value.
        Idempotent: a second call never overwrites an operator's edits."""
        if not self.is_set():
            self.set(
                region=location.region,
                ui_lang=location.ui_lang,
                language=location.language,
                gdelt_country=location.gdelt_country,
                city=location.city,
                latlong=location.latlong,
            )
        return self.get()
