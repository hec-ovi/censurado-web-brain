"""Editorial seeders the bootstrap runs, plus their default fixtures.

A fresh box has empty tables. The bootstrap seeds only the parts of the editorial
config that are author-agnostic on their own: the publication location and the default
house style guide. It does NOT invent authors or news sources. ``DEFAULT_PERSONAS``
and ``DEFAULT_PORTALS`` are empty, so an empty database
stays empty until an operator creates personas and registers portals (via the panel,
the API, or their own private seed). Author identities and the specific outlets a
newsroom trusts are operator-owned data, never shipped in tracked code.

Every seeder is FIND-OR-CREATE keyed on a stable identity (persona id, portal domain,
the single location row, an active style version), so re-running the bootstrap is a
no-op that never clobbers an operator's later edits. The defaults stay overridable (the
``*_seed`` parameters) so a test, or an operator's private seed script, can drive the
same code with explicit fixtures.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from newsroom.editorial.location import DEFAULT_LOCATION, Location, LocationStore
from newsroom.editorial.portals import Portal, PortalStore
from newsroom.editorial.style import StyleGuide, StyleStore
from newsroom.personas.store import Persona, PersonaStore

__all__ = [
    "DEFAULT_PORTALS",
    "DEFAULT_PERSONAS",
    "DEFAULT_STYLE",
    "SeedResult",
    "seed_location",
    "seed_portals",
    "seed_personas",
    "seed_style",
    "seed_all",
]


# The specific outlets a newsroom trusts are operator-owned data: they live in the
# operator's database, registered from the panel/API, never shipped in tracked code.
# The default is empty, so an empty database has zero portals. Overridable per call (the
# ``portals`` seed parameter) for tests and an operator's private seed script.
DEFAULT_PORTALS: tuple[Portal, ...] = ()


# Author identities are operator-owned data: they live ONLY in the operator's database,
# created on demand (the CLI ``create-author`` flow or the panel), never shipped in
# tracked code. The default is empty, so an empty database has zero personas until the
# operator adds them. Overridable per call (the ``personas`` seed parameter) for tests
# and an operator's private seed script.
DEFAULT_PERSONAS: tuple[Persona, ...] = ()


# The default house style guide (the "model of redaction + rules") lives in a tracked
# DATA file, not as a .py literal: voice prose, a good and a labeled BAD exemplar, an
# ordered list of positive imperatives, and the mechanically enforceable lexicon /
# sourcing / structure (including ``min_sources``). It is author-agnostic and operator-
# overridable from the panel; the file is the editable default, not code. NO length
# phrasing anywhere (no "en N palabras"): brevity is qualitative, never a word cap.
_DEFAULT_STYLE_PATH: Path = Path(__file__).resolve().parent / "default_style.json"


def _load_default_style() -> StyleGuide:
    """Build the default house style from the tracked ``default_style.json`` data file."""
    data = json.loads(_DEFAULT_STYLE_PATH.read_text(encoding="utf-8"))
    return StyleGuide(
        voice=data.get("voice", ""),
        exemplars=data.get("exemplars", []),
        rules=data.get("rules", []),
        lexicon=data.get("lexicon", {}),
        sourcing=data.get("sourcing", {}),
        structure=data.get("structure", {}),
    )


DEFAULT_STYLE = _load_default_style()


@dataclass
class SeedResult:
    """What a bootstrap seed run did, per category, so the summary is honest about
    created-versus-skipped and a re-run can be seen to be a no-op."""

    location_created: bool = False
    portals_created: list[str] | None = None
    portals_skipped: list[str] | None = None
    personas_created: list[str] | None = None
    personas_skipped: list[str] | None = None
    style_created: bool = False

    def as_dict(self) -> dict:
        return {
            "location_created": self.location_created,
            "portals_created": self.portals_created or [],
            "portals_skipped": self.portals_skipped or [],
            "personas_created": self.personas_created or [],
            "personas_skipped": self.personas_skipped or [],
            "style_created": self.style_created,
        }


def seed_location(conn: sqlite3.Connection, location: Location = DEFAULT_LOCATION) -> bool:
    """Seed the publication location if none is stored. Returns True if it created it."""
    store = LocationStore(conn)
    already = store.is_set()
    store.seed(location)
    return not already


def seed_portals(conn: sqlite3.Connection, portals=DEFAULT_PORTALS) -> tuple[list[str], list[str]]:
    """Find-or-create each portal by domain. Returns (created_ids, skipped_ids)."""
    store = PortalStore(conn)
    created: list[str] = []
    skipped: list[str] = []
    for portal in portals:
        existing = store.get_by_domain(portal.domain)
        if existing is not None:
            skipped.append(existing.id)
            continue
        created.append(store.create(portal).id)
    return created, skipped


def seed_personas(conn: sqlite3.Connection, personas=DEFAULT_PERSONAS) -> tuple[list[str], list[str]]:
    """Find-or-create each persona by id. Returns (created_ids, skipped_ids). Never
    overwrites an existing persona, so an operator's rich edits are preserved."""
    store = PersonaStore(conn)
    created: list[str] = []
    skipped: list[str] = []
    for persona in personas:
        persona_id = persona.id or ""
        if persona_id and store.get(persona_id) is not None:
            skipped.append(persona_id)
            continue
        created.append(store.create(persona).id)
    return created, skipped


def seed_style(conn: sqlite3.Connection, style: StyleGuide = DEFAULT_STYLE) -> bool:
    """Seed the default house style as the active version if none is active yet.
    Returns True if it created it. A later operator version is never replaced."""
    store = StyleStore(conn)
    if store.active() is not None:
        return False
    store.add_version(style, created_by="seed", activate=True)
    return True


def seed_all(
    conn: sqlite3.Connection,
    *,
    location: Location = DEFAULT_LOCATION,
    portals=DEFAULT_PORTALS,
    personas=DEFAULT_PERSONAS,
    style: StyleGuide = DEFAULT_STYLE,
) -> SeedResult:
    """Seed location, portals, personas, and the house style over one connection,
    idempotently. Returns a per-category summary of what was created versus skipped."""
    location_created = seed_location(conn, location)
    portals_created, portals_skipped = seed_portals(conn, portals)
    personas_created, personas_skipped = seed_personas(conn, personas)
    style_created = seed_style(conn, style)
    return SeedResult(
        location_created=location_created,
        portals_created=portals_created,
        portals_skipped=portals_skipped,
        personas_created=personas_created,
        personas_skipped=personas_skipped,
        style_created=style_created,
    )
