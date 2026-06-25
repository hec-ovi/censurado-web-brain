"""The default fixtures and the idempotent seeders.

A fresh DB gets a working newsroom (location, portals, personas, an active style); a
re-run creates nothing and never overwrites an operator's edits; and overrides let a
caller seed a subset. Drives the seeders and the stores' public API only.
"""

from __future__ import annotations

from newsroom.db import open_db
from newsroom.editorial import LocationStore, PortalStore, StyleStore
from newsroom.editorial.seeds import (
    DEFAULT_PERSONAS,
    DEFAULT_PORTALS,
    DEFAULT_STYLE,
    seed_all,
)
from newsroom.personas import Persona, PersonaStore


def test_seed_all_populates_a_fresh_box():
    conn = open_db(":memory:")
    result = seed_all(conn)

    assert result.location_created is True
    assert LocationStore(conn).get().region == "AR"

    portal_ids = [p.id for p in PortalStore(conn).list()]
    assert "clarin-com" in portal_ids and "infobae-com" in portal_ids
    assert len(result.portals_created) == len(DEFAULT_PORTALS)

    persona_ids = {p.id for p in PersonaStore(conn).list()}
    assert {"lara-arianna", "borge-luis-jorge", "glorieta-sadeta", "vector-omni"} <= persona_ids

    assert result.style_created is True
    active = StyleStore(conn).active()
    assert active is not None and active.version == 1


def test_seed_all_is_idempotent_on_rerun():
    conn = open_db(":memory:")
    seed_all(conn)
    again = seed_all(conn)

    assert again.location_created is False
    assert again.style_created is False
    assert again.portals_created == []
    assert again.personas_created == []
    assert set(again.portals_skipped) == {p.id for p in PortalStore(conn).list()}
    assert set(again.personas_skipped) == {p.id for p in PersonaStore(conn).list()}
    # No duplicates crept in.
    assert len(PortalStore(conn).list()) == len(DEFAULT_PORTALS)
    assert len(PersonaStore(conn).list()) == len(DEFAULT_PERSONAS)


def test_seed_personas_never_overwrites_an_operator_edit():
    conn = open_db(":memory:")
    # An operator already authored a rich lara-arianna before bootstrap re-runs.
    PersonaStore(conn).create(
        Persona(
            id="lara-arianna",
            display_name="Lara Arianna",
            beat="politics",
            who_i_am="Soy Lara, version del operador, mucho mas rica y personal.",
            style="La voz del operador.",
        )
    )
    result = seed_all(conn)

    assert "lara-arianna" in result.personas_skipped
    assert "lara-arianna" not in result.personas_created
    kept = PersonaStore(conn).get("lara-arianna")
    assert kept.who_i_am.startswith("Soy Lara, version del operador")  # untouched


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


# ----- default content sanity -----


def test_default_personas_cover_every_beat():
    beats = {p.beat for p in DEFAULT_PERSONAS}
    assert beats == {"tech", "world", "politics", "economics"}


def test_default_personas_carry_a_negative_exemplar():
    assert all(p.few_shots_neg for p in DEFAULT_PERSONAS)


def test_default_style_is_coherent_and_uncapped():
    # A bad exemplar (the strongest lever), gate rules, real sourcing, and crucially NO
    # length cap: no rule or structure text imposes a word/character count.
    assert any(e["label"] == "bad" for e in DEFAULT_STYLE.exemplars)
    assert any(r["severity"] == "gate" for r in DEFAULT_STYLE.rules)
    assert DEFAULT_STYLE.sourcing["min_sources"] == 2

    texts = " ".join(
        [DEFAULT_STYLE.voice, *(r["text"] for r in DEFAULT_STYLE.rules), *DEFAULT_STYLE.structure.values()]
    ).lower()
    # No "en N palabras" / "N words" style length cap phrasing.
    import re

    assert re.search(r"\d+\s*(palabra|word|caracter|character)", texts) is None


def test_default_style_banned_terms_present():
    assert "demoledor" in DEFAULT_STYLE.lexicon["banned_terms"]
    assert DEFAULT_STYLE.lexicon["preferred_swaps"]["polemico"] == "discutido"
