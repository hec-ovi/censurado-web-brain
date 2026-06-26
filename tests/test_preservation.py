"""The verbatim-preservation gate: a pure, deterministic check that the draft did not
invent a date's year or respell a proper name away from the sources.

No model runs here: every case builds a ledger and an entity list and asserts the
PreservationResult directly, the same way the citation tests exercise CitationVerify.
The cross-language case is the load-bearing one: a year present in an English snippet
must NOT be flagged when the body that quotes it is Spanish.
"""

from __future__ import annotations

from datetime import datetime, timezone

from newsroom.pipeline.preservation import preservation_check
from newsroom.research.ledger import Ledger


def _ledger(*claims_snippets: tuple[str, str]) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    for i, (claim, snippet) in enumerate(claims_snippets):
        led.add(claim=claim, url=f"https://src.test/{i}", snippet=snippet)
    return led


def test_clean_body_with_grounded_year_and_entity_is_ok():
    led = _ledger(("Milei tomo posesion en 2023", "El presidente asumio el 10 de diciembre de 2023"))
    res = preservation_check(
        "El presidente Javier Milei asumio el cargo en 2023.",
        ledger=led, entities=["Javier Milei"],
    )
    assert res.ok
    assert res.altered_dates == []
    assert res.corrupted_entities == []


def test_body_year_absent_from_all_sources_is_flagged():
    led = _ledger(("Milei tomo posesion en 2023", "Asumio en diciembre de 2023"))
    res = preservation_check(
        "El recorte se anuncio el 15 de marzo de 2025, segun el comunicado.",
        ledger=led, entities=[],
    )
    assert not res.ok
    assert res.altered_dates == ["2025"]


def test_year_present_in_english_source_is_not_flagged_for_a_spanish_body():
    # The year is language-invariant: it appears in an English wire snippet, and the
    # Spanish body quotes the same digits, so the gate must NOT flag it.
    led = _ledger(("Milei took office", "Argentina's president was sworn in on December 10, 2023"))
    res = preservation_check(
        "El presidente asumio el 10 de diciembre de 2023.",
        ledger=led, entities=[],
    )
    assert res.ok
    assert res.altered_dates == []


def test_corrupted_entity_is_reported_with_the_body_spelling():
    led = _ledger(("Milei anuncio medidas", "Javier Milei firmo el decreto"))
    res = preservation_check(
        "El presidente Javier Miley anuncio nuevas medidas economicas.",
        ledger=led, entities=["Javier Milei"],
    )
    assert not res.ok
    assert res.corrupted_entities == [{"expected": "Javier Milei", "found": "Javier Miley"}]


def test_entity_absent_with_no_near_variant_is_not_flagged():
    # The draft simply did not mention the entity; absence is an editorial choice,
    # never a corruption, so nothing is flagged.
    led = _ledger(("Kirchner hablo", "Cristina Kirchner dio un discurso"))
    res = preservation_check(
        "El presidente Javier Milei recorrio la planta industrial hoy.",
        ledger=led, entities=["Cristina Kirchner"],
    )
    assert res.ok
    assert res.corrupted_entities == []


def test_short_entity_never_generates_a_near_miss():
    # "UN" is too short to anchor a name check: it would collide with ordinary prose,
    # so it is skipped even though the body is full of two-letter words near it.
    led = _ledger(("La UN se reunio", "Un comunicado de la UN"))
    res = preservation_check(
        "El un dia de hoy la un on an un texto cualquiera.",
        ledger=led, entities=["UN"],
    )
    assert res.ok
    assert res.corrupted_entities == []


def test_standalone_year_as_quantity_is_not_flagged():
    # "2000" reads as a magnitude ("2000 millones de pesos"), not a year: a unit word
    # follows it, so the bare-year pass leaves it alone even though 2000 is in no source.
    led = _ledger(("El plan moviliza fondos", "Un presupuesto elevado para la obra"))
    res = preservation_check(
        "El plan destina 2000 millones de pesos a la obra publica.",
        ledger=led, entities=[],
    )
    assert res.ok
    assert res.altered_dates == []


def test_bare_year_with_date_cue_is_still_flagged():
    # "en 2025" is a real date: a cue ("en") precedes it and no unit follows, so the
    # ungrounded year (2025 is in no source) is still flagged.
    led = _ledger(("Algo paso en 2023", "Un hecho ocurrido en 2023"))
    res = preservation_check(
        "La medida se aprobo en 2025, segun el comunicado oficial.",
        ledger=led, entities=[],
    )
    assert not res.ok
    assert res.altered_dates == ["2025"]


def test_numeric_date_year_absent_from_sources_is_flagged():
    # A fully numeric date exercises the explicit form: its year is taken
    # unconditionally and, being in no source, is flagged.
    led = _ledger(("Firmado en 2023", "El acto se realizo en 2023"))
    res = preservation_check(
        "El contrato se firmo el 12/05/2024 por la tarde.",
        ledger=led, entities=[],
    )
    assert not res.ok
    assert res.altered_dates == ["2024"]


def test_entity_first_char_change_is_not_a_respelling():
    # "Alberto Garcia" is a different person from "Roberto Garcia" (R != A), not a
    # respelling, so the first-char-per-token guard (and source-grounding) keep it from
    # being flagged: a revise must never rename one person into another.
    led = _ledger(("Garcia hablo", "Alberto Garcia dio una conferencia"))
    res = preservation_check(
        "El diputado Alberto Garcia presento el proyecto de ley.",
        ledger=led, entities=["Roberto Garcia"],
    )
    assert res.ok
    assert res.corrupted_entities == []


def test_entity_first_char_guard_holds_even_when_source_names_the_entity():
    # Even with "Roberto Garcia" in the sources, a body that says "Alberto Garcia" is a
    # different name (R != A), not a respelling: the first-char guard blocks the rename.
    led = _ledger(("Garcia hablo", "Roberto Garcia dio una conferencia"))
    res = preservation_check(
        "El diputado Alberto Garcia presento el proyecto de ley.",
        ledger=led, entities=["Roberto Garcia"],
    )
    assert res.ok
    assert res.corrupted_entities == []


def test_entity_respelling_absent_from_sources_is_not_flagged():
    # The body has a near-miss spelling (Javier Miley, J=J/M=M) but the correct spelling
    # (Javier Milei) is in NO source, so the sources cannot ground the rename and the
    # gate stays silent: it never propagates a manager-side spelling into the article.
    led = _ledger(("Un anuncio presidencial", "El presidente firmo el decreto"))
    res = preservation_check(
        "El presidente Javier Miley anuncio nuevas medidas economicas.",
        ledger=led, entities=["Javier Milei"],
    )
    assert res.ok
    assert res.corrupted_entities == []
