"""Research: the websearch-skill tool wrapper, the claim-source ledger, and the
bounded plan-then-search loop."""

from __future__ import annotations

from .ledger import Ledger, LedgerRow
from .loop import ResearchOutcome, plan_subquestions, run_research
from .tool import ResearchTool, SearchResult

__all__ = [
    "Ledger",
    "LedgerRow",
    "ResearchTool",
    "SearchResult",
    "ResearchOutcome",
    "plan_subquestions",
    "run_research",
]
