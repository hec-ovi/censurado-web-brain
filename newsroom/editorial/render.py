"""Render a StyleGuide into prompt text for the drafter and the evaluator.

The SAME guide feeds both calls. The drafter gets the voice prose, the rules in scope
for drafting (in priority order), and the good and bad exemplars (the contrast a weak
local model leans on). The evaluator gets the gate rules in scope for review as a
checklist plus the sourcing requirements, so it judges against the same rules the
writer was told to follow. Mechanically checkable rules (the banned lexicon) are
enforced deterministically in code (``editorial.lexicon``), never by asking the model
to "not say" a word.

The scaffolding is English (like the rest of the journalist prompts), while the rule
texts pass through verbatim in whatever language the operator wrote them (Spanish, by
default). There is NO length phrasing here: brevity, where the rules call for it, is
qualitative.
"""

from __future__ import annotations

from newsroom.editorial.style import StyleGuide

__all__ = ["style_for_draft", "style_for_eval"]


def _rules_in_scope(guide: StyleGuide, scopes: tuple[str, ...]) -> list[str]:
    return [
        str(r.get("text", "")).strip()
        for r in guide.rules
        if isinstance(r, dict) and r.get("scope") in scopes and str(r.get("text", "")).strip()
    ]


def _exemplars(guide: StyleGuide, label: str) -> list[str]:
    return [
        str(e.get("text", "")).strip()
        for e in guide.exemplars
        if isinstance(e, dict) and e.get("label") == label and str(e.get("text", "")).strip()
    ]


def style_for_draft(guide: StyleGuide | None) -> str:
    """The house style as drafting guidance: voice, the draft-scope rules in priority
    order, and the good/bad exemplars. Returns "" when there is no guide, so the prompt
    token simply disappears."""
    if guide is None:
        return ""
    parts: list[str] = []
    if guide.voice.strip():
        parts.append("House style (the publication's voice; write within it):\n" + guide.voice.strip())

    rules = _rules_in_scope(guide, ("draft", "both"))
    if rules:
        parts.append(
            "Rules, in priority order:\n"
            + "\n".join(f"{i}. {text}" for i, text in enumerate(rules, 1))
        )

    good = _exemplars(guide, "good")
    if good:
        parts.append("Write like these:\n" + "\n".join(f"- {t}" for t in good))
    bad = _exemplars(guide, "bad")
    if bad:
        parts.append("Do NOT write like these:\n" + "\n".join(f"- {t}" for t in bad))

    swaps = (guide.lexicon or {}).get("preferred_swaps") or {}
    if isinstance(swaps, dict) and swaps:
        parts.append("Prefer: " + "; ".join(f"{v} (not {k})" for k, v in swaps.items()))

    return "\n\n".join(parts)


def style_for_eval(guide: StyleGuide | None) -> str:
    """The house style as an evaluator checklist: the eval-scope rules, the sourcing
    requirements, and the banned lexicon. Returns "" when there is no guide."""
    if guide is None:
        return ""
    parts: list[str] = []
    rules = _rules_in_scope(guide, ("eval", "both"))
    if rules:
        parts.append(
            "House style rules the draft must satisfy (list any it fails in failing_sections):\n"
            + "\n".join(f"- {text}" for text in rules)
        )

    sourcing = guide.sourcing or {}
    requirements: list[str] = []
    min_sources = sourcing.get("min_sources")
    if min_sources:
        requirements.append(f"at least {min_sources} independent sources support the central fact")
    if sourcing.get("require_attribution"):
        requirements.append("every claim is attributed to a named source")
    if sourcing.get("no_fabricated_quotes"):
        requirements.append("no fabricated quotes")
    if requirements:
        parts.append("Sourcing requirements: " + "; ".join(requirements) + ".")

    banned = (guide.lexicon or {}).get("banned_terms") or []
    if banned:
        parts.append("Banned terms (the draft must not use any): " + ", ".join(str(b) for b in banned) + ".")

    return "\n\n".join(parts)
