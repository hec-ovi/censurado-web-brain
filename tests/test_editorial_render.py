"""Rendering the house style into prompt text and the banned-lexicon check. Pure
functions; no model, no DB connection beyond building a StyleGuide value.
"""

from __future__ import annotations

from newsroom.editorial import banned_terms_found, style_for_draft, style_for_eval
from newsroom.editorial.seeds import DEFAULT_STYLE


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
