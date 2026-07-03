"""The default fixtures and the idempotent seeders.

The shipped defaults are EMPTY of authors and news sources: ``DEFAULT_PERSONAS`` and
``DEFAULT_PORTALS`` are empty, so a fresh DB gets only the author-agnostic config
(location, an active house style) and zero personas/portals until an operator adds
them. Explicit ``*_seed`` overrides let a caller (or an operator's
private seed script) populate a subset; a re-run creates nothing and never overwrites an
edit. Drives the seeders and the stores' public API only.
"""

from __future__ import annotations

from newsroom.db import open_db
from newsroom.editorial import LocationStore, Portal, PortalStore, StyleStore
from newsroom.editorial.seeds import (
    DEFAULT_PERSONAS,
    DEFAULT_PORTALS,
    DEFAULT_STYLE,
    seed_all,
)
from newsroom.personas import Persona, PersonaStore

# Synthetic fixtures: invented authors/domains used only to exercise the seeders. They
# resemble no operator's real newsroom; the shipped defaults are empty.
_PERSONAS = (
    Persona(
        id="ada-lovelace", display_name="Ada Lovelace", beat="politics",
        who_i_am="Cubre el poder y las instituciones.", style="Declarativas claras.",
        few_shots_neg=[{"prompt": "x", "bad": "y"}],
    ),
    Persona(
        id="ida-rivers", display_name="Ida Rivers", beat="economics",
        who_i_am="Traduce precios y tasas.", style="Cifras primero.",
    ),
)
_PORTALS = (
    Portal(domain="example.com", homepage="https://example.com", ownership_group="group-a"),
    Portal(domain="news.test", homepage="https://news.test", ownership_group="group-b"),
)


def test_defaults_ship_empty_of_authors_and_sources():
    # The acceptance invariant: nothing about who writes or what they read lives in code.
    assert DEFAULT_PERSONAS == ()
    assert DEFAULT_PORTALS == ()


def test_seed_all_with_empty_defaults_creates_no_authors_or_sources():
    # The shipped defaults seed location + style, but zero personas/portals.
    conn = open_db(":memory:")
    result = seed_all(conn)

    assert result.location_created is True
    assert result.style_created is True
    assert result.personas_created == []
    assert result.portals_created == []
    assert PersonaStore(conn).list() == []
    assert PortalStore(conn).list() == []


def test_seed_all_populates_a_fresh_box():
    conn = open_db(":memory:")
    result = seed_all(conn, personas=_PERSONAS, portals=_PORTALS)

    assert result.location_created is True
    assert LocationStore(conn).get().region == "AR"

    portal_ids = [p.id for p in PortalStore(conn).list()]
    assert "example-com" in portal_ids and "news-test" in portal_ids
    assert len(result.portals_created) == len(_PORTALS)

    persona_ids = {p.id for p in PersonaStore(conn).list()}
    assert {"ada-lovelace", "ida-rivers"} <= persona_ids

    assert result.style_created is True
    active = StyleStore(conn).active()
    assert active is not None and active.version == 1


def test_seed_all_is_idempotent_on_rerun():
    conn = open_db(":memory:")
    seed_all(conn, personas=_PERSONAS, portals=_PORTALS)
    again = seed_all(conn, personas=_PERSONAS, portals=_PORTALS)

    assert again.location_created is False
    assert again.style_created is False
    assert again.portals_created == []
    assert again.personas_created == []
    assert set(again.portals_skipped) == {p.id for p in PortalStore(conn).list()}
    assert set(again.personas_skipped) == {p.id for p in PersonaStore(conn).list()}
    # No duplicates crept in.
    assert len(PortalStore(conn).list()) == len(_PORTALS)
    assert len(PersonaStore(conn).list()) == len(_PERSONAS)


def test_seed_personas_never_overwrites_an_operator_edit():
    conn = open_db(":memory:")
    # An operator already authored a rich ada-lovelace before a re-seed runs with the
    # same id in its explicit fixtures.
    PersonaStore(conn).create(
        Persona(
            id="ada-lovelace",
            display_name="Ada Lovelace",
            beat="politics",
            who_i_am="Version del operador, mucho mas rica y personal.",
            style="La voz del operador.",
        )
    )
    result = seed_all(conn, personas=_PERSONAS, portals=())

    assert "ada-lovelace" in result.personas_skipped
    assert "ada-lovelace" not in result.personas_created
    kept = PersonaStore(conn).get("ada-lovelace")
    assert kept.who_i_am.startswith("Version del operador")  # untouched


def test_seed_does_not_clobber_an_edited_location_or_style():
    conn = open_db(":memory:")
    LocationStore(conn).set(region="UY", city="Montevideo")
    StyleStore(conn).add_version(DEFAULT_STYLE, created_by="operator", activate=True)

    result = seed_all(conn)

    assert result.location_created is False and result.style_created is False
    assert LocationStore(conn).get().region == "UY"
    assert StyleStore(conn).active().created_by == "operator"


def test_seed_overrides_let_a_subset_be_seeded():
    conn = open_db(":memory:")
    one = Persona(id="solo", display_name="Solo", beat="tech", who_i_am="x", style="y")
    result = seed_all(conn, personas=(one,), portals=())

    assert result.personas_created == ["solo"]
    assert result.portals_created == []
    assert [p.id for p in PersonaStore(conn).list()] == ["solo"]


# ----- default house style sanity (the house style default is kept; authors/sources are not) -----


def test_default_style_is_coherent_and_uncapped():
    # A bad exemplar (the strongest lever), gate rules, real sourcing, and crucially NO
    # length cap: no rule or structure text imposes a word/character count.
    assert any(e["label"] == "bad" for e in DEFAULT_STYLE.exemplars)
    assert any(r["severity"] == "gate" for r in DEFAULT_STYLE.rules)
    assert DEFAULT_STYLE.sourcing["min_sources"] == 6
    assert DEFAULT_STYLE.sourcing["min_per_type"] == 2

    texts = " ".join(
        [DEFAULT_STYLE.voice, *(r["text"] for r in DEFAULT_STYLE.rules), *(str(v) for v in DEFAULT_STYLE.structure.values())]
    ).lower()
    # No "en N palabras" / "N words" style length cap phrasing.
    import re

    assert re.search(r"\d+\s*(palabra|word|caracter|character)", texts) is None


def test_default_style_banned_terms_present():
    assert "demoledor" in DEFAULT_STYLE.lexicon["banned_terms"]
    assert DEFAULT_STYLE.lexicon["preferred_swaps"]["polemico"] == "discutido"


def test_default_style_is_loaded_from_a_data_file_not_a_py_literal():
    # The house style default lives in a tracked JSON data file (operator-editable
    # config), not as a Python literal, so the prompt-like prose is out of the .py.
    from newsroom.editorial import seeds

    assert seeds._DEFAULT_STYLE_PATH.exists()
    assert seeds._DEFAULT_STYLE_PATH.suffix == ".json"
    assert DEFAULT_STYLE.voice  # loaded and non-empty
    assert DEFAULT_STYLE.sourcing["min_sources"] == 6
