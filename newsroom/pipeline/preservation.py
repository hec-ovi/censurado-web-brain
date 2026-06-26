"""Verbatim preservation: a deterministic guard that the draft did not invent or
mangle the two facts a model is most likely to quietly corrupt, dates and names.

A local model paraphrasing under a persona drifts on specifics: it nudges a year
(2023 -> 2024), or respells a proper name it half-remembers (Milei -> Miley). These
are not citation problems (the URL is fine) and not style problems, so neither the
citation gate nor the lexicon gate catches them. This module is pure code, no model:
it checks the body's dates and named entities against the claim-source ledger and
reports what does not line up, so the existing fact-check revise can restore it.

Two language-invariance choices keep it false-positive-free across the cross-language
sources the newsroom gathers (an English wire snippet feeding a Spanish article):

  * Dates are reduced to their 4-digit YEAR. "2024" is "2024" in every language, so a
    year is the only part of a date that can be checked against a foreign-language
    source without tripping on translation ("15 de marzo" vs "March 15"). A date with
    no 4-digit year carries no language-invariant anchor and is left alone. A BARE
    four-digit year is only read as a date when a date cue precedes it and no magnitude
    unit follows it, so "2000 millones de pesos" stays a quantity and never forces a
    revise; the long-form ("15 de marzo de 2024") and numeric ("12/05/2024") forms are
    dates by shape, so their year is taken unconditionally.
  * Names are flagged only on a NEAR MISS, never on absence, and only when the SOURCES
    spell the name. An entity the draft simply did not mention is an editorial choice,
    not a corruption; only a token window that is almost-but-not-quite the entity (a
    respelling) is. The sources, not the manager's entity list, are the authority for a
    name's spelling: if the sources do not contain the expected name the gate stays
    silent, so it can never rename a different person into the listed entity nor
    propagate a manager-side typo into the article.

The flaggable unit is intentionally narrow on both gates: we would rather miss a real
corruption than force a revise on a false alarm, since the revise spends budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from newsroom.research.ledger import Ledger

__all__ = ["PreservationResult", "preservation_check"]

_MONTHS = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
    "octubre|noviembre|diciembre"
)
# Explicit date forms in the BODY: a long-form ("15 de marzo de 2024") or a fully
# numeric date ("12/05/2024"). Both are dates by shape, so their 4-digit year is taken
# unconditionally; their spans are recorded so a year inside them is not re-judged as a
# bare year below.
_DATE = re.compile(
    rf"\b\d{{1,2}}\s+de\s+(?:{_MONTHS})(?:\s+de\s+\d{{4}})?\b"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    re.IGNORECASE,
)
# A standalone 19xx/20xx year. Year-shaped numbers are also quantities ("2000 millones
# de pesos"), so a bare match is a date only in a date context (see _bare_year_is_date).
_BARE_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# The language-invariant anchor inside an explicit date token: a 19xx/20xx year.
_YEAR = re.compile(r"(?:19|20)\d{2}")
# Words that, immediately after a bare year, mark it as a magnitude, not a date.
_MAGNITUDE_WORDS = frozenset(
    "millones millon millón mill mil pesos peso dolares dólares dolar dólar euros euro "
    "toneladas tonelada kilometros kilómetros metros personas habitantes votos "
    "empleados puntos unidades casos viviendas hectareas hectáreas kilos litros "
    "ejemplares".split()
)
# Words that, in the immediately preceding slot (ignoring a leading "el"), mark a bare
# year as a date. "de"/"del" are deliberately excluded as too generic; the long-form
# form already covers "X de YYYY".
_DATE_CUES = frozenset("en año años desde hasta hacia durante circa para entre".split())
# A near-miss is a respelling, not a different name; below this normalized similarity
# the window is a different string and we do not claim it is the corrupted entity.
_NEAR_MISS_SIMILARITY = 0.82
# Short tokens ("de", "la", "UN") collide with too much ordinary prose to anchor a
# name check, so an entity must clear this length AND own one token this long.
_MIN_ENTITY_LEN = 4


@dataclass
class PreservationResult:
    ok: bool
    altered_dates: list[str] = field(default_factory=list)
    corrupted_entities: list[dict] = field(default_factory=list)


def _levenshtein(a: str, b: str) -> int:
    """Edit distance via a bounded two-row DP (no third-party dep). Two rows are all
    the gate needs: it only wants the final distance, never the alignment, so the full
    matrix would be wasted memory."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[len(b)]


def _similarity(a: str, b: str) -> float:
    """Normalized similarity in [0, 1]: 1 - distance / longest. Normalizing by length
    makes the threshold mean the same thing for a short name and a long one."""
    longest = max(len(a), len(b))
    return 1.0 if longest == 0 else 1.0 - _levenshtein(a, b) / longest


def _source_text(ledger: Ledger) -> tuple[str, str]:
    """One lowercased blob of every row's claim + snippet, plus a digits-only copy.
    The blob backs substring checks; the digits-only copy makes a year ground even if
    the source wrote it next to punctuation a plain substring would not span."""
    blob = " ".join(f"{row.claim} {row.snippet}" for row in ledger.rows).lower()
    return blob, re.sub(r"\D", "", blob)


def _bare_year_is_date(body: str, start: int, end: int) -> bool:
    """Whether the bare year at ``body[start:end]`` reads as a date, not a quantity.
    True only when a date cue precedes it (``en``/``año``/``desde``/...) AND no magnitude
    unit follows it (``millones``/``pesos``/...), which is what tells "en 2025" (a year)
    apart from "2000 millones de pesos" (a quantity). ``re`` lookbehind is fixed-width,
    so the neighbouring words are inspected by hand over the match positions instead of
    in the pattern; a leading "el" before the cue is stepped over ("para el 2025")."""
    after = re.match(r"\s?(\w+)", body[end:])
    if after and after.group(1).lower() in _MAGNITUDE_WORDS:
        return False
    prev_words = re.findall(r"\w+", body[:start].lower())
    preceding = prev_words[-1] if prev_words else ""
    if preceding == "el" and len(prev_words) >= 2:
        preceding = prev_words[-2]
    return preceding in _DATE_CUES


def _altered_dates(body: str, source_text: str, source_digits: str) -> list[str]:
    """The body years that appear in no source, deduped in body order. A year is the
    only flaggable part of a date (language-invariant); a token without one is skipped
    because it has no anchor that survives translation. Explicit date forms contribute
    their year unconditionally; a bare year only when its context reads as a date."""
    altered: list[str] = []

    def record(year: str) -> None:
        if year not in altered and year not in source_text and year not in source_digits:
            altered.append(year)

    consumed: list[tuple[int, int]] = []
    for match in _DATE.finditer(body):
        consumed.append(match.span())
        ym = _YEAR.search(match.group(0))
        if ym:
            record(ym.group(0))
    for match in _BARE_YEAR.finditer(body):
        start, end = match.span()
        if any(s <= start < e for s, e in consumed):
            continue  # the year sits inside an explicit date form, already counted
        if _bare_year_is_date(body, start, end):
            record(match.group(0))
    return altered


def _is_flaggable_entity(entity: str) -> bool:
    """Whether an entity is specific enough to anchor a name check: long enough overall
    and owning at least one token long enough that an accidental window match in prose
    is implausible. Filters out particles and acronyms ("de", "UN") that would be noise."""
    if len(entity) < _MIN_ENTITY_LEN:
        return False
    return any(len(tok) >= _MIN_ENTITY_LEN for tok in entity.split())


def _corrupted_entity(entity: str, body_tokens: list[str], body_collapsed: str) -> dict | None:
    """A near-miss respelling of ``entity`` in the body, or None. Verbatim presence (or
    pure absence) returns None; only a same-length token window that is close-but-not-
    equal AND keeps the first character of EVERY token of ``entity`` is reported, as
    ``{"expected": entity, "found": <body spelling>}``. The first-char-per-token guard
    separates a respelling ("Javier Milei" -> "Javier Miley", J=J/M=M) from a different
    person ("Roberto Garcia" -> "Alberto Garcia", R!=A), which a rename must not touch.
    The best (most similar) window wins, first one on ties, so the revise gets the
    clearest fix."""
    tokens = entity.split()
    target = " ".join(tokens).lower()
    if target in body_collapsed:
        return None
    width = len(tokens)
    first_chars = [tok[0].lower() for tok in tokens]
    best_found: str | None = None
    best_sim = _NEAR_MISS_SIMILARITY
    for i in range(len(body_tokens) - width + 1):
        window = body_tokens[i : i + width]
        if any(w[0].lower() != fc for w, fc in zip(window, first_chars)):
            continue
        candidate = " ".join(window)
        lowered = candidate.lower()
        if lowered == target:
            continue
        sim = _similarity(lowered, target)
        if sim > best_sim:
            best_sim, best_found = sim, candidate
    if best_found is None:
        return None
    return {"expected": entity, "found": best_found}


def preservation_check(body: str, *, ledger: Ledger, entities: list[str]) -> PreservationResult:
    """Check the body kept the sources' dates and names verbatim. Pure, no model call.

    ``altered_dates`` are body years grounded in no source (invented or shifted).
    ``corrupted_entities`` are assignment entities the draft respelled (a near-miss
    window in the body) AND whose correct spelling the sources actually carry, never
    ones it merely omitted nor ones the sources never name. ``ok`` when both are empty,
    so the caller can short-circuit the revise exactly as the citation gate does."""
    source_text, source_digits = _source_text(ledger)
    source_collapsed = " ".join(source_text.split())
    altered = _altered_dates(body, source_text, source_digits)

    body_tokens = body.split()
    body_collapsed = " ".join(body_tokens).lower()
    corrupted: list[dict] = []
    for entity in entities:
        e = entity.strip()
        if not e or not _is_flaggable_entity(e):
            continue
        # The sources, not the manager's list, are the authority for a name's spelling:
        # only ground a corruption when the expected name is in the sources, so the
        # revise can never rename a different person into it nor carry a typo through.
        if " ".join(e.split()).lower() not in source_collapsed:
            continue
        hit = _corrupted_entity(e, body_tokens, body_collapsed)
        if hit is not None:
            corrupted.append(hit)

    return PreservationResult(
        ok=not altered and not corrupted,
        altered_dates=altered,
        corrupted_entities=corrupted,
    )
