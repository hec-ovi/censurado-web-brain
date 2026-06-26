"""Deterministic lexicon enforcement: the mechanically checkable part of the house style.

Asking a local model to "never use word X" is unreliable (negation backfires), so the
banned lexicon is checked in plain code instead and gated at review: if a banned term
appears in a draft, the evaluator forces a REVISE regardless of the model's verdict.
Matching is case-insensitive and word-bounded, so a banned ``brutal`` does not trip on
``brutalidad`` and multi-word terms (``sin precedentes``) match as a phrase.
"""

from __future__ import annotations

import re

__all__ = ["banned_terms_found"]


def banned_terms_found(text: str, lexicon: dict | None) -> list[str]:
    """The banned terms from ``lexicon`` that appear in ``text`` (case-insensitive,
    word-bounded), in lexicon order, de-duplicated. Empty when there is no lexicon."""
    if not lexicon:
        return []
    banned = lexicon.get("banned_terms") or []
    found: list[str] = []
    for term in banned:
        t = str(term).strip()
        if not t or t in found:
            continue
        if re.search(rf"\b{re.escape(t)}\b", text, flags=re.IGNORECASE):
            found.append(t)
    return found
