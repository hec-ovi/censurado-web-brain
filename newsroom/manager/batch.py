"""The batch planner: fan the existing manager across authors, once per desk.

A managed run points ONE manager at a global discovery search and clamps the result
to ``n_max``. A batch sweep instead runs that SAME manager ONCE PER AUTHOR over a
search scoped to that author's OWN sources (``discover(persona, freshness=...)``, the
factory ``deps._author_discovery`` builds), so the conspiracy desk and the tech desk
list topics from the outlets each actually reads and surface different stories. The
per-author manifests are then interleaved (round robin) and clamped to an overall
ceiling.

This is the batch's ONLY departure from a managed run. Everything else, topic
clustering, the coverage triage, the per-author ``n_max`` clamp, is the manager's,
reused wholesale: ``plan_batch`` is a fan, not a second manager. Two deliberate
properties follow:

  * NO cross-author dedup. Each author's manager triages against the same published
    ``coverage`` (so nobody re-runs a story the site already covered), but NOT against
    what a sibling author just planned. Two desks covering the same event from
    different angles is the point of a multi-desk newsroom, not a bug to collapse.
  * One stuck desk never sinks the batch. ``run_manager`` never raises (a cap or its
    circuit breaker forces a terminal and degrades to whatever it ranked), so a desk
    that finds nothing contributes an empty slice and the others still ship.

``dispatch_run`` stays the SOLE fan-out point: ``plan_batch`` only produces the
``Manifest`` the runner then dispatches, exactly as ``run_manager`` does for a managed
run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from newsroom.inference.provider import ProviderConfig
from newsroom.manager.coverage import CoverageItem
from newsroom.manager.manager import NewsSearch, run_manager
from newsroom.manager.types import AssignmentSpec, Manifest
from newsroom.personas import Persona

__all__ = ["plan_batch"]


def _interleave(rows: list[list[AssignmentSpec]]) -> list[AssignmentSpec]:
    """Round-robin flatten the per-author assignment slices: every author's first pick,
    then every author's second, and so on. So when an overall ceiling truncates the
    batch it keeps one article from each desk before any desk's second, rather than
    spending the whole budget on whichever author happened to come first."""
    out: list[AssignmentSpec] = []
    depth = max((len(row) for row in rows), default=0)
    for i in range(depth):
        for row in rows:
            if i < len(row):
                out.append(row[i])
    return out


def plan_batch(
    *,
    personas: list[Persona],
    coverage: list[CoverageItem],
    discover: Callable[..., NewsSearch],
    cfg: ProviderConfig,
    prompts_dir: Path | str,
    overrides: dict[str, str] | None = None,
    per_author: int = 2,
    timeframe_freshness: str | None = None,
    max_steps: int = 8,
    circuit_breaker: int = 3,
    duplicate_threshold: float = 0.6,
    followup_threshold: float = 0.3,
    max_total: int | None = None,
    budget=None,
) -> Manifest:
    """Plan a batch by running the manager once per author and merging the results.

    For each persona, ``discover(persona, freshness=timeframe_freshness)`` yields that
    author's source-scoped topic search; ``run_manager`` then triages it against the
    shared ``coverage`` and clamps to ``n_max=per_author``. The per-author manifests are
    interleaved round-robin and, when ``max_total`` is set, clamped to that overall
    ceiling (``None`` leaves the natural ``per_author * len(personas)`` bound in place).

    Returns one merged ``Manifest``: ``forced`` is True if ANY desk forced a terminal,
    and ``steps`` / ``dropped_duplicates`` / ``candidates_considered`` sum across desks,
    so the report still reflects the whole sweep's work."""
    rows: list[list[AssignmentSpec]] = []
    forced_any = False
    dropped = 0
    candidates = 0
    steps_total = 0
    for persona in personas:
        search = discover(persona, freshness=timeframe_freshness)
        manifest = run_manager(
            personas=[persona],
            coverage=coverage,
            search_news=search,
            cfg=cfg,
            prompts_dir=prompts_dir,
            overrides=overrides,
            n_max=per_author,
            max_steps=max_steps,
            circuit_breaker=circuit_breaker,
            duplicate_threshold=duplicate_threshold,
            followup_threshold=followup_threshold,
            budget=budget,
        )
        rows.append(list(manifest.assignments))
        forced_any = forced_any or manifest.forced
        dropped += manifest.dropped_duplicates
        candidates += manifest.candidates_considered
        steps_total += manifest.steps

    interleaved = _interleave(rows)
    if max_total is not None:
        interleaved = interleaved[: max(0, max_total)]
    return Manifest(
        assignments=interleaved,
        stop_reason="emitted",
        forced=forced_any,
        steps=steps_total,
        dropped_duplicates=dropped,
        candidates_considered=candidates,
    )
