"""Fact-check: a deterministic grounding gate over the ledger, plus one bounded revise.

The gate is pure code, no model, and has two halves that share one revise:

  * CitationVerify: a published article must not cite a URL that is not in the
    claim-source ledger (a fabricated citation), and must carry no unresolved
    authoring markers (``TODO``, ``FIXME``, ``TK``, ``[citation needed]``).
  * PreservationCheck (``preservation.py``): the article must not invent or shift a
    date's year, nor respell a proper name, away from what the sources say.

This is the article's grounding gate. If EITHER half fails, the pipeline routes to a
SINGLE bounded revise (one persona-blind model call that rewrites against only the
ledger's sources), not an open re-search loop (A.8). Both halves fold into that ONE
revise: their problems are concatenated into the same prompt, so a citation fault and
a corrupted name are fixed in the same pass. The revise is bounded to one pass whether
or not it fully clears the issues; that keeps the stage's cost bounded and debited from
the shared article budget.

Runs PERSONA-BLIND: persona framing measurably degrades factual accuracy, so the
verification pass drops the voice and checks claims plainly (agentic-refinements
appendix).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from newsroom.inference import ChatRequest, chat
from newsroom.inference.provider import ProviderConfig
from newsroom.pipeline.context import ledger_text
from newsroom.pipeline.preservation import PreservationResult, preservation_check
from newsroom.prompts import load_prompt, render
from newsroom.research.ledger import Ledger

__all__ = ["CitationResult", "GroundingResult", "citation_verify", "fact_check"]

# A URL in the body. Trailing sentence punctuation is trimmed so "see https://a/x."
# matches the ledger's "https://a/x".
_URL = re.compile(r"https?://[^\s<>()\[\]\"']+")
_TRAILING = ".,;:!?)]}\"'"
_TODO_MARKERS = re.compile(r"\bTODO\b|\bFIXME\b|\bTK\b|\[citation needed\]", re.IGNORECASE)


@dataclass
class CitationResult:
    ok: bool
    unsupported_urls: list[str] = field(default_factory=list)
    todo_markers: list[str] = field(default_factory=list)


@dataclass
class GroundingResult:
    """The combined verdict of the two pure halves of the grounding gate. ``ok`` is
    true only when BOTH the citation check and the preservation check pass, so the
    caller routes to the revise iff something is ungrounded."""

    ok: bool
    citation: CitationResult
    preservation: PreservationResult


def _urls_in(body: str) -> list[str]:
    return [m.group(0).rstrip(_TRAILING) for m in _URL.finditer(body)]


def citation_verify(body: str, ledger: Ledger) -> CitationResult:
    """Check the body grounds in the ledger: no cited URL is absent from the ledger,
    and no unresolved authoring marker remains. Deterministic, no model call."""
    unsupported = sorted({url for url in _urls_in(body) if not ledger.has_url(url)})
    todos = sorted({m.group(0) for m in _TODO_MARKERS.finditer(body)})
    return CitationResult(ok=not unsupported and not todos,
                          unsupported_urls=unsupported, todo_markers=todos)


def _grounding(body: str, ledger: Ledger, entities: list[str]) -> GroundingResult:
    """Run both pure halves of the gate over one body and combine their verdicts."""
    citation = citation_verify(body, ledger)
    preservation = preservation_check(body, ledger=ledger, entities=entities)
    return GroundingResult(ok=citation.ok and preservation.ok,
                           citation=citation, preservation=preservation)


def fact_check(
    body: str,
    *,
    ledger: Ledger,
    entities=(),
    cfg: ProviderConfig,
    prompts_dir,
    budget=None,
    overrides: dict[str, str] | None = None,
) -> tuple[str, GroundingResult]:
    """Verify the body against the ledger (citations AND verbatim preservation); on
    failure, one bounded persona-blind revise that carries BOTH halves' problems in a
    single prompt. Returns the (possibly revised) body and the grounding result for the
    body that is returned (so the caller can see whether it still has open issues)."""
    entities = list(entities)
    result = _grounding(body, ledger, entities)
    if result.ok:
        return body, result

    template = load_prompt(prompts_dir, "journalist", "factcheck.md", overrides=overrides)
    problems = []
    if result.citation.unsupported_urls:
        problems.append("Citations not backed by an approved source: " + ", ".join(result.citation.unsupported_urls))
    if result.citation.todo_markers:
        problems.append("Unresolved authoring markers: " + ", ".join(result.citation.todo_markers))
    if result.preservation.altered_dates:
        problems.append(
            "Dates not found in the sources (do not invent or alter dates; use only "
            "dates that appear in the sources): " + ", ".join(result.preservation.altered_dates)
        )
    for corrupt in result.preservation.corrupted_entities:
        problems.append(
            "Names altered from the sources (restore the exact source spelling): "
            f"{corrupt['expected']} (written as {corrupt['found']})"
        )
    prompt = render(
        template,
        article=body,
        sources=ledger_text(ledger),
        problems="\n".join(problems),
    )
    response = chat(ChatRequest(messages=[{"role": "user", "content": prompt}], temperature=0.2), cfg=cfg)
    if budget is not None:
        budget.debit_response(response)
    revised = response.content
    return revised, _grounding(revised, ledger, entities)
