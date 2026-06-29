"""Rendering pipeline artifacts (the ledger, a persona) into prompt text.

Handoffs between pipeline nodes are ARTIFACTS, not transcripts (A.7): a node
receives the ledger and, for the draft, the persona, rendered as plain text. These
helpers are the single place that shaping happens, so the drafter, evaluator, and
fact-checker all present the ledger identically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from newsroom.personas import Persona
from newsroom.research.ledger import Ledger

__all__ = ["ledger_text", "persona_block", "EditorialContext"]


def _no_ownership(domain: str) -> str:
    """The default ownership resolver: every domain is unknown (``""``), so the
    corroboration gate falls back to per-domain sentinels. Used as the dataclass default
    in place of a bare lambda so an ``EditorialContext`` built without a real resolver is
    still picklable and introspectable, and behaves exactly as 'no co-ownership known'."""
    return ""


@dataclass
class EditorialContext:
    """The operator-owned editorial inputs threaded into the per-article pipeline: the
    house style (rendered for the drafter and the evaluator), the banned lexicon (a
    deterministic review gate), a digest of recent coverage (so the writer does not
    repeat published stories), and the cross-source corroboration inputs (the minimum
    number of INDEPENDENT sources the ledger must rest on, plus the resolver that maps a
    domain to its ownership group so co-owned outlets count once). All fields default
    empty/off, so a run with no style guide or no prior coverage behaves exactly as
    before (the prompt tokens render to nothing); ``min_independent_sources`` of 0 means
    the corroboration gate is OFF and no assignment is ever dropped for it."""

    house_style_draft: str = ""
    house_style_eval: str = ""
    style_lexicon: dict = field(default_factory=dict)
    recent_coverage: str = ""
    # The same recent-coverage items rendered into ``recent_coverage``, kept structured
    # (each carries a slug + fingerprint) so the widget step can emit ONE
    # ``{{relacionado:slug}}`` card for the best-matching prior article. ``CoverageItem``
    # is left untyped here (a plain list) to avoid a pipeline<->manager import cycle;
    # empty (the default) means no related card is ever emitted.
    related_coverage: list = field(default_factory=list)
    min_independent_sources: int = 0
    ownership_of: Callable[[str], str] = _no_ownership


def ledger_text(ledger: Ledger) -> str:
    """The claim-source ledger as a bulleted source list for a prompt."""
    lines = [f"- {row.url} | {row.claim}: {row.snippet}".rstrip() for row in ledger.rows]
    return "\n".join(lines) if lines else "(no sources were gathered)"


def _examples(label: str, items: list) -> str:
    # A hand-built Persona could pass a bare string here; treat it as no examples
    # rather than iterating it character by character.
    if not items or isinstance(items, str):
        return ""
    body = "\n".join(f"  - {str(item).strip()}" for item in items if str(item).strip())
    return f"{label}:\n{body}\n" if body else ""


def persona_block(persona: Persona) -> str:
    """The persona, rendered for FRESH re-injection on every draft call (the
    'helpful assistant' attractor lives in the weights, so a persona stated once
    drifts back to neutral over a long generation, A-refinements). Includes the
    contrastive positive AND negative exemplars that local models lean on."""
    parts = [
        f"You ARE {persona.display_name}, the journalist who owns the {persona.beat} beat.",
        f"You ALWAYS write in {persona.language}. Never switch languages.",
        f"Who you are: {persona.who_i_am}",
    ]
    if persona.about:
        parts.append(f"About you: {persona.about}")
    parts.append(f"Your voice and style: {persona.style}")
    pos = _examples("Write LIKE these (your voice)", persona.few_shots_pos)
    neg = _examples("Do NOT write like these (not your voice)", persona.few_shots_neg)
    if pos:
        parts.append(pos.rstrip())
    if neg:
        parts.append(neg.rstrip())
    return "\n".join(parts)
