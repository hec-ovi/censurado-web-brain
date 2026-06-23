"""Fact-check: a deterministic CitationVerify over the ledger, plus one bounded revise.

The verify step is pure code, no model: a published article must not cite a URL
that is not in the claim-source ledger (a fabricated citation), and must carry no
unresolved authoring markers (``TODO``, ``FIXME``, ``TK``, ``[citation needed]``).
This is the article's grounding gate. If it fails, the pipeline routes to a SINGLE
bounded revise (one persona-blind model call that rewrites against only the
ledger's sources), not an open re-search loop (A.8). The revise is bounded to one
pass whether or not it fully clears the issues; that keeps the stage's cost
bounded and debited from the shared article budget.

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
from newsroom.prompts import load_prompt, render
from newsroom.research.ledger import Ledger

__all__ = ["CitationResult", "citation_verify", "fact_check"]

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


def _urls_in(body: str) -> list[str]:
    return [m.group(0).rstrip(_TRAILING) for m in _URL.finditer(body)]


def citation_verify(body: str, ledger: Ledger) -> CitationResult:
    """Check the body grounds in the ledger: no cited URL is absent from the ledger,
    and no unresolved authoring marker remains. Deterministic, no model call."""
    unsupported = sorted({url for url in _urls_in(body) if not ledger.has_url(url)})
    todos = sorted({m.group(0) for m in _TODO_MARKERS.finditer(body)})
    return CitationResult(ok=not unsupported and not todos,
                          unsupported_urls=unsupported, todo_markers=todos)


def fact_check(
    body: str,
    *,
    ledger: Ledger,
    cfg: ProviderConfig,
    prompts_dir,
    budget=None,
) -> tuple[str, CitationResult]:
    """Verify the body against the ledger; on failure, one bounded persona-blind
    revise. Returns the (possibly revised) body and the verify result for the body
    that is returned (so the caller can see whether it still has open issues)."""
    result = citation_verify(body, ledger)
    if result.ok:
        return body, result

    template = load_prompt(prompts_dir, "journalist", "factcheck.md")
    problems = []
    if result.unsupported_urls:
        problems.append("Citations not backed by an approved source: " + ", ".join(result.unsupported_urls))
    if result.todo_markers:
        problems.append("Unresolved authoring markers: " + ", ".join(result.todo_markers))
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
    return revised, citation_verify(revised, ledger)
