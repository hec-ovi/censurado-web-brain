"""Rendering the house style into prompt text, the banned-lexicon check, and the
recent-coverage digest. Pure functions; no model, no DB connection beyond building a
StyleGuide / CoverageItem value.
"""

from __future__ import annotations

from newsroom.editorial import banned_terms_found, style_for_draft, style_for_eval
from newsroom.editorial.seeds import DEFAULT_STYLE
from newsroom.manager.coverage import CoverageItem, recent_coverage_text


# ----- style_for_draft -----


def test_style_for_draft_is_empty_without_a_guide():
    assert style_for_draft(None) == ""


def test_style_for_draft_includes_voice_rules_and_exemplars():
    out = style_for_draft(DEFAULT_STYLE)
    assert "House style" in out
    assert DEFAULT_STYLE.voice[:20] in out
    # A draft-or-both rule appears; the good and bad exemplars appear.
    assert "Atribui cada afirmacion" in out
    assert "Write like these:" in out and "Do NOT write like these:" in out
    assert "El Banco Central subio la tasa" in out  # good exemplar
    assert "demoledor" in out  # bad exemplar text (the contrast lever)


def test_style_for_draft_omits_eval_only_rules():
    guide = DEFAULT_STYLE.__class__(
        voice="v",
        rules=[
            {"id": "d", "text": "DRAFT ONLY RULE", "severity": "preference", "scope": "draft", "check": "llm"},
            {"id": "e", "text": "EVAL ONLY RULE", "severity": "gate", "scope": "eval", "check": "llm"},
        ],
    )
    out = style_for_draft(guide)
    assert "DRAFT ONLY RULE" in out
    assert "EVAL ONLY RULE" not in out


# ----- style_for_eval -----


def test_style_for_eval_is_empty_without_a_guide():
    assert style_for_eval(None) == ""


def test_style_for_eval_is_a_checklist_with_sourcing_and_banned_terms():
    out = style_for_eval(DEFAULT_STYLE)
    assert "failing_sections" in out  # it instructs the evaluator to flag failures
    assert "at least 5 independent sources" in out
    assert "no fabricated quotes" in out
    assert "Banned terms" in out and "demoledor" in out
    # An eval-or-both rule is present; a draft-only rule (titulo-directo) is not.
    assert "Atribui cada afirmacion" in out
    assert "titulo breve" not in out


# ----- banned lexicon -----


def test_banned_terms_found_is_word_bounded_and_case_insensitive():
    lex = {"banned_terms": ["brutal", "sin precedentes"]}
    assert banned_terms_found("Un ataque BRUTAL sin precedentes.", lex) == ["brutal", "sin precedentes"]
    # word-bounded: 'brutal' must not trip inside 'brutalidad'
    assert banned_terms_found("Hablo de brutalidad institucional.", lex) == []


def test_banned_terms_found_empty_without_lexicon():
    assert banned_terms_found("anything", None) == []
    assert banned_terms_found("anything", {}) == []


# ----- recent coverage digest -----


def _cov(section, headline, topics=None):
    return CoverageItem(section=section, headline=headline, topics=topics or [])


def test_recent_coverage_text_empty_when_nothing_published():
    assert recent_coverage_text([]) == ""


def test_recent_coverage_text_lists_section_headline_topics():
    out = recent_coverage_text([
        _cov("politics", "Reforma aprobada", ["congreso", "reforma"]),
        _cov("economics", "Sube la tasa"),
    ])
    assert "Do NOT repeat" in out
    assert "- politics: Reforma aprobada [topics: congreso, reforma]" in out
    assert "- economics: Sube la tasa" in out


def test_recent_coverage_text_honors_limit():
    items = [_cov("tech", f"Story {i}") for i in range(5)]
    out = recent_coverage_text(items, limit=2)
    assert "Story 0" in out and "Story 1" in out
    assert "Story 2" not in out
