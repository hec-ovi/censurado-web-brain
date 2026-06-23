"""Rendering pipeline artifacts (the ledger, a persona) into prompt text.

Handoffs between pipeline nodes are ARTIFACTS, not transcripts (A.7): a node
receives the ledger and, for the draft, the persona, rendered as plain text. These
helpers are the single place that shaping happens, so the drafter, evaluator, and
fact-checker all present the ledger identically.
"""

from __future__ import annotations

from newsroom.personas import Persona
from newsroom.research.ledger import Ledger

__all__ = ["ledger_text", "persona_block"]


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
