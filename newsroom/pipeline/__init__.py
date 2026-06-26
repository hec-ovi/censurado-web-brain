"""The per-article pipeline: outline, draft/evaluate sweeps, enrich, fact-check, finalize.

The orchestrator (``run_article_pipeline``) drives one assignment through the
bounded sweep loop and the persona-blind verification passes to a finalized,
validated publish payload, or drops it when the shared budget is exhausted. The
sub-stages are exposed for direct testing and for the run orchestrator (Step 6/10).
"""

from __future__ import annotations

from newsroom.pipeline.article import ArticleOutcome, run_article_pipeline
from newsroom.pipeline.budget import ArticleBudget
from newsroom.pipeline.evaluate import Evaluation, evaluate_draft
from newsroom.pipeline.factcheck import CitationResult, GroundingResult, citation_verify, fact_check
from newsroom.pipeline.finalize import finalize_article
from newsroom.pipeline.preservation import PreservationResult, preservation_check

__all__ = [
    "ArticleBudget",
    "ArticleOutcome",
    "CitationResult",
    "Evaluation",
    "GroundingResult",
    "PreservationResult",
    "citation_verify",
    "preservation_check",
    "evaluate_draft",
    "fact_check",
    "finalize_article",
    "run_article_pipeline",
]
