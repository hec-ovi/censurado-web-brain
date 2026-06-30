"""Editorial seeders the bootstrap runs, plus their default fixtures.

A fresh box has empty tables. The bootstrap seeds only the parts of the editorial
config that are author-agnostic on their own: the publication location, the default
house style guide, and the on-disk prompt library. It does NOT invent authors or news
sources. ``DEFAULT_PERSONAS`` and ``DEFAULT_PORTALS`` are empty, so an empty database
stays empty until an operator creates personas and registers portals (via the console,
the API, or their own private seed). Author identities and the specific outlets a
newsroom trusts are operator-owned data, never shipped in tracked code.

Every seeder is FIND-OR-CREATE keyed on a stable identity (persona id, portal domain,
the single location row, an active style version), so re-running the bootstrap is a
no-op that never clobbers an operator's later edits. The defaults stay overridable (the
``*_seed`` parameters) so a test, or an operator's private seed script, can drive the
same code with explicit fixtures.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from newsroom.editorial.location import DEFAULT_LOCATION, Location, LocationStore
from newsroom.editorial.portals import Portal, PortalStore
from newsroom.editorial.prompts_store import PromptStore
from newsroom.editorial.style import StyleGuide, StyleStore
from newsroom.personas.store import Persona, PersonaStore

__all__ = [
    "DEFAULT_PORTALS",
    "DEFAULT_PERSONAS",
    "DEFAULT_STYLE",
    "DEFAULT_PROMPTS_DIR",
    "SeedResult",
    "seed_location",
    "seed_portals",
    "seed_personas",
    "seed_style",
    "seed_prompts",
    "seed_all",
]

# The on-disk prompt library the seeder lifts into the versioned prompt store. Same value
# config.Settings defaults ``prompts_dir`` to (the repo's ``prompts/``); bootstrap passes
# ``settings.prompts_dir`` explicitly, this default keeps ``seed_all`` self-contained.
DEFAULT_PROMPTS_DIR: Path = Path(__file__).resolve().parents[2] / "prompts"


# The specific outlets a newsroom trusts are operator-owned data: they live in the
# operator's database, registered from the console/API, never shipped in tracked code.
# The default is empty, so an empty database has zero portals. Overridable per call (the
# ``portals`` seed parameter) for tests and an operator's private seed script.
DEFAULT_PORTALS: tuple[Portal, ...] = ()


# Author identities are operator-owned data: they live ONLY in the operator's database,
# created on demand (the CLI ``create-author`` flow or the console), never shipped in
# tracked code. The default is empty, so an empty database has zero personas until the
# operator adds them. Overridable per call (the ``personas`` seed parameter) for tests
# and an operator's private seed script.
DEFAULT_PERSONAS: tuple[Persona, ...] = ()


# The default house style guide (the "model of redaction + rules"). Voice prose, one
# good and one labeled BAD exemplar, an ordered list of positive imperatives, and the
# mechanically enforceable lexicon / sourcing / structure. NO length phrasing anywhere
# (no "en N palabras"): brevity is expressed qualitatively, never as a word cap.
DEFAULT_STYLE = StyleGuide(
    voice=(
        "Escribimos noticias en espanol neutro rioplatense, sobrias y verificables. "
        "Contamos el hecho antes que la reaccion, atribuimos cada afirmacion a una "
        "fuente nombrada y separamos lo confirmado de lo que una sola parte sostiene. "
        "No militamos ni adornamos: si el dato es fuerte, no necesita adjetivos."
    ),
    exemplars=[
        {
            "label": "good",
            "text": "El Banco Central subio la tasa al 40 por ciento, segun su comunicado oficial.",
            "why": "Hecho concreto, cifra, fuente nombrada, sin carga emotiva.",
        },
        {
            "label": "bad",
            "text": "En una decision demoledora, el Central volvio a castigar a los ahorristas.",
            "why": "Adjetivacion sensacionalista, sin fuente, toma partido.",
        },
    ],
    rules=[
        {"id": "hecho-primero", "text": "Abri con el hecho central y su consecuencia concreta.", "severity": "gate", "scope": "both", "check": "llm"},
        {"id": "atribuir", "text": "Atribui cada afirmacion factica a una fuente nombrada.", "severity": "gate", "scope": "both", "check": "llm"},
        {"id": "dos-fuentes", "text": "Apoya el hecho central en al menos dos fuentes independientes.", "severity": "gate", "scope": "both", "check": "code"},
        {"id": "confirmado", "text": "Distingui lo confirmado de lo que afirma una sola parte.", "severity": "gate", "scope": "both", "check": "llm"},
        {"id": "sin-inventar", "text": "Usa solo datos y citas presentes en las fuentes reunidas.", "severity": "gate", "scope": "both", "check": "code"},
        {"id": "neutral", "text": "Manten un tono sobrio y describi sin tomar partido.", "severity": "gate", "scope": "both", "check": "llm"},
        {"id": "titulo-directo", "text": "Escribi un titulo breve y directo: el hecho esencial, sin relleno.", "severity": "preference", "scope": "draft", "check": "llm"},
        {"id": "cifras-con-fuente", "text": "Acompana cada cifra con su fuente y su fecha.", "severity": "preference", "scope": "both", "check": "llm"},
        {"id": "contexto-local", "text": "Da el contexto que el lector local necesita, sin asumir que ya lo sabe.", "severity": "preference", "scope": "draft", "check": "llm"},
        {"id": "sin-jerga", "text": "Explica cualquier termino tecnico la primera vez que aparece.", "severity": "preference", "scope": "draft", "check": "llm"},
        {"id": "no-repetir", "text": "Aporta lo nuevo del dia y enlaza la cobertura previa relacionada.", "severity": "preference", "scope": "both", "check": "llm"},
        {"id": "cierre-util", "text": "Cierra con lo que sigue o lo que aun no se sabe, no con una opinion.", "severity": "preference", "scope": "draft", "check": "llm"},
    ],
    lexicon={
        "banned_terms": ["demoledor", "escandaloso", "letal", "brutal", "sin precedentes", "increible", "no te lo podes perder"],
        "preferred_swaps": {"polemico": "discutido", "fulmino": "rechazo", "castigo": "afecto", "historico": "destacado"},
    },
    sourcing={"min_sources": 5, "require_attribution": True, "no_fabricated_quotes": True},
    structure={
        "headline": "Breve y directo: el hecho esencial, sin relleno ni adjetivos.",
        "dateline": "CIUDAD, fecha, al inicio del cuerpo.",
        "lede": "Primer parrafo con que paso, quien, cuando y por que importa.",
    },
)


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
    prompts_created: list[str] | None = None
    prompts_skipped: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "location_created": self.location_created,
            "portals_created": self.portals_created or [],
            "portals_skipped": self.portals_skipped or [],
            "personas_created": self.personas_created or [],
            "personas_skipped": self.personas_skipped or [],
            "style_created": self.style_created,
            "prompts_created": self.prompts_created or [],
            "prompts_skipped": self.prompts_skipped or [],
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


def seed_prompts(
    conn: sqlite3.Connection, prompts_dir: Path | str = DEFAULT_PROMPTS_DIR
) -> tuple[list[str], list[str]]:
    """Lift each ``<role>/<name>.md`` prompt file into the versioned prompt store as the
    active v1 of its key (the relative path with forward slashes, e.g.
    ``journalist/research.md``). Find-or-create per key: a key that already has an active
    version is never replaced, so an operator's later edit is preserved. Returns
    (created_keys, skipped_keys)."""
    store = PromptStore(conn)
    root = Path(prompts_dir)
    created: list[str] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*.md")):
        key = path.relative_to(root).as_posix()
        if store.active(key) is not None:
            skipped.append(key)
            continue
        store.add_version(key, path.read_text(encoding="utf-8"), created_by="seed", activate=True)
        created.append(key)
    return created, skipped


def seed_all(
    conn: sqlite3.Connection,
    *,
    location: Location = DEFAULT_LOCATION,
    portals=DEFAULT_PORTALS,
    personas=DEFAULT_PERSONAS,
    style: StyleGuide = DEFAULT_STYLE,
    prompts_dir: Path | str = DEFAULT_PROMPTS_DIR,
) -> SeedResult:
    """Seed location, portals, personas, the house style, and the prompt library over one
    connection, idempotently. Returns a per-category summary of what was created versus
    skipped."""
    location_created = seed_location(conn, location)
    portals_created, portals_skipped = seed_portals(conn, portals)
    personas_created, personas_skipped = seed_personas(conn, personas)
    style_created = seed_style(conn, style)
    prompts_created, prompts_skipped = seed_prompts(conn, prompts_dir)
    return SeedResult(
        location_created=location_created,
        portals_created=portals_created,
        portals_skipped=portals_skipped,
        personas_created=personas_created,
        personas_skipped=personas_skipped,
        style_created=style_created,
        prompts_created=prompts_created,
        prompts_skipped=prompts_skipped,
    )
