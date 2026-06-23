"""The deterministic fan-out dispatch: the ONE place a run turns the manager's
manifest into running journalists (architecture doc B.2). Journalists never spawn
agents, so this function is the sole fan-out point, which is what makes runaway
recursion structurally impossible rather than merely unlikely (A.3).

It persists each ``AssignmentSpec`` as a real ``assignments`` row, then runs the
per-article pipeline (Step 5) for each, collecting outcomes in manifest order
(never completion order, so a run is reproducible). Concurrency is 1-2 locally
(GPU KV-cache), realized with a small thread pool. That thread pool also satisfies
Step 5's deferred requirement: the finalize seam's pydantic-ai ``run_sync`` runs in
a worker thread here, never on an event loop, and when this whole function is
later driven from an async route (Steps 8/10) the caller offloads it with
``run_in_threadpool`` so the loop is never blocked.

A single ``lock`` serializes the brain's one shared SQLite connection across the
concurrent pipelines and the request loop (passed straight through to
``run_article_pipeline``, which already holds it only around store writes). One
article failing for any reason is isolated to that article: it is dropped and the
run continues, mirroring the pipeline's own finalize-failure isolation.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from newsroom.inference.provider import ProviderConfig
from newsroom.manager.types import AssignmentSpec, Manifest
from newsroom.personas import Persona
from newsroom.pipeline import ArticleBudget, ArticleOutcome, run_article_pipeline
from newsroom.research.ledger import Ledger
from newsroom.runs import Assignment, RunStore

__all__ = ["DispatchResult", "dispatch_run"]

# Builds the grounding ledger for one assignment. In production this runs the
# research loop (Step 4) for the assignment's angle, DEBITING the per-article budget
# it is handed (so research charges the same shared ceiling as the pipeline, A.8);
# tests inject a ready ledger and ignore the budget.
LedgerBuilder = Callable[[Assignment, AssignmentSpec, ArticleBudget], Ledger]


@dataclass
class DispatchResult:
    run_id: str
    assignments: list[Assignment]
    outcomes: list[ArticleOutcome]


def dispatch_run(
    *,
    run_id: str,
    manifest: Manifest,
    persona_index: dict[str, Persona],
    store: RunStore,
    make_ledger: LedgerBuilder,
    drafter_cfg: ProviderConfig,
    evaluator_cfg: ProviderConfig,
    finalize_cfg: ProviderConfig,
    prompts_dir: Path | str,
    budget_factory: Callable[[], ArticleBudget],
    max_sweeps: int = 4,
    concurrency: int = 1,
    lock: threading.Lock | None = None,
) -> DispatchResult:
    """Persist the manifest's assignments and run the per-article pipeline for each.

    ``persona_index`` maps persona id -> ``Persona`` (loaded by the caller).
    ``budget_factory`` mints a FRESH per-article budget for each assignment (the
    budget is per-article, not per-run). Returns the persisted rows and their
    outcomes, aligned by index."""
    guard = lock if lock is not None else nullcontext()

    # 1. Persist the specs as assignment rows (serial, under the lock).
    assignments: list[Assignment] = []
    for spec in manifest.assignments:
        with guard:
            row = store.create_assignment(
                run_id=run_id, persona_id=spec.persona_id, section=spec.section, angle=spec.angle
            )
        assignments.append(row)

    if not assignments:
        return DispatchResult(run_id=run_id, assignments=[], outcomes=[])

    def _run_one(idx: int) -> ArticleOutcome:
        row = assignments[idx]
        try:
            persona = persona_index[row.persona_id]
            # Mint ONE per-article budget before research, then hand the SAME budget to
            # research (via make_ledger) and the pipeline, so the article's whole cost
            # (research + outline + sweeps + enrich + fact-check + finalize) charges one
            # ceiling, and the wall-clock starts before research (A.8/B.2).
            budget = budget_factory()
            ledger = make_ledger(row, manifest.assignments[idx], budget)
            return run_article_pipeline(
                assignment=row,
                persona=persona,
                ledger=ledger,
                store=store,
                budget=budget,
                drafter_cfg=drafter_cfg,
                evaluator_cfg=evaluator_cfg,
                finalize_cfg=finalize_cfg,
                prompts_dir=prompts_dir,
                max_sweeps=max_sweeps,
                lock=lock,
            )
        except Exception:
            # Any unexpected failure (research, a transport error, a missing persona)
            # is isolated to THIS article so it never crashes the run. Mark it dropped
            # and return a dropped outcome; the body was never published.
            with guard:
                try:
                    store.mark_dropped(row.id, reason="error")
                except Exception:
                    pass
            return ArticleOutcome(
                assignment_id=row.id, status="dropped", stop_reason="error", drafts=0,
                ledger_digest="",
            )

    # 2. Fan out (the SOLE fan-out point). The thread pool keeps run_sync off any
    # event loop; results are collected in manifest order, not completion order.
    workers = max(1, min(concurrency, len(assignments)))
    outcomes: list[ArticleOutcome | None] = [None] * len(assignments)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for idx, outcome in zip(
            range(len(assignments)), pool.map(_run_one, range(len(assignments)))
        ):
            outcomes[idx] = outcome

    return DispatchResult(
        run_id=run_id, assignments=assignments, outcomes=[o for o in outcomes if o is not None]
    )
