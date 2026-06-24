"""The run-execution core: ONE function, three entry points, trigger-blind.

This is the deterministic OUTER workflow the whole harness hangs off (architecture
doc B.2/B.7): resolve roles -> manager triage -> fan-out dispatch -> publish the
ready articles. The brain is TRIGGER-BLIND: the only thing the automation layer
ever picks is a ``mode`` (``manual`` | ``express`` | ``managed``), and that enum is
the entire trigger surface. So the brain has no knowledge of how it was triggered,
and the automation layer (a periodic external trigger in v1, a UI later) can change
without touching it. The brain itself names no scheduler, by design.

The mode collapses to a ``RunScope`` (a fan-out ceiling and an optional persona
subset) in ``plan_run`` BEFORE the run executes. ``execute_run`` then takes the
already-resolved scope and runs the IDENTICAL path regardless of mode: it never
branches on the mode (it only stamps it onto the run record for provenance). That
is what makes "all three entry points take the same run-execution path" a
structural fact rather than a convention, and it is asserted by a source guard in
the tests.

Dependencies are INJECTED via ``RunDeps`` so a test drives this real path against
the shared fake. Production assembles the deps from settings + the environment in
``newsroom.runner.deps``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from newsroom.config import Settings
from newsroom.manager import dispatch_run, run_manager
from newsroom.manager.coverage import CoverageStore
from newsroom.manager.dispatch import LedgerBuilder
from newsroom.manager.manager import NewsSearch
from newsroom.manager.preflight import ResolvedRoles
from newsroom.manager.types import Manifest
from newsroom.personas import Persona, PersonaStore
from newsroom.pipeline import ArticleBudget, ArticleOutcome
from newsroom.publish import publish_assignment, publish_batch_assignments
from newsroom.runs import Run, RunStore

__all__ = [
    "RUN_MODES",
    "RunScope",
    "RunDeps",
    "PublishOutcome",
    "RunReport",
    "plan_run",
    "start_run",
    "execute_run",
    "run_now",
    "run_manual",
    "run_express",
    "run_managed",
]

# The entire trigger surface: the automation layer picks exactly one of these.
RUN_MODES = ("manual", "express", "managed")


@dataclass(frozen=True)
class RunScope:
    """The resolved scope of a run. The mode collapses to these knobs: ``n`` (the
    fan-out ceiling for this run, never above the configured ``n_max``),
    ``persona_ids`` (a subset to draw from, or None for all personas), and
    ``generate_images`` (per-run override for the art-director image step: True/False,
    or None to defer to ``settings.auto_generate_image``)."""

    n: int
    persona_ids: tuple[str, ...] | None = None
    generate_images: bool | None = None


@dataclass
class RunDeps:
    """Everything ``execute_run`` needs, injected so the real path is testable.

    ``search_news`` (query -> candidate stories) and ``make_ledger`` (build the
    grounding ledger for one assignment) are the two seams that reach the network in
    production (web search + the research loop); a test injects in-process doubles so
    the run never leaves the box. ``lock`` serializes the brain's one shared SQLite
    connection across the request loop and the background run thread."""

    store: RunStore
    persona_store: PersonaStore
    coverage_store: CoverageStore
    roles: ResolvedRoles
    search_news: NewsSearch
    make_ledger: LedgerBuilder
    publish_base_url: str
    operator_token: str
    prompts_dir: Path | str
    settings: Settings
    lock: threading.Lock | None = None
    # The art-director image seam (article -> hero image). None disables image
    # generation entirely; a test injects an in-process double. Whether it runs for a
    # given run is gated by the run scope + settings.auto_generate_image in execute_run.
    illustrate: Callable[..., object] | None = None


@dataclass
class PublishOutcome:
    """The result of publishing one ready article."""

    assignment_id: str
    ok: bool
    published_id: str | None = None
    slug: str | None = None
    replayed: bool = False
    code: str = ""


@dataclass
class RunReport:
    """The full record of one run: the manager's manifest, the per-article pipeline
    outcomes (ready/dropped), and the publish outcomes for the ones that finalized."""

    run_id: str
    mode: str
    status: str  # done | done_with_errors | failed
    manifest: Manifest
    outcomes: list[ArticleOutcome] = field(default_factory=list)
    published: list[PublishOutcome] = field(default_factory=list)


def plan_run(
    mode: str,
    *,
    n_max: int,
    express_n: int,
    n: int | None = None,
    persona_ids: list[str] | None = None,
    images: bool | None = None,
) -> RunScope:
    """Resolve a mode (plus optional operator overrides) into a ``RunScope``. This is
    the ONE place the mode is consulted: the trigger surface. The modes differ ONLY
    in scope, not in the path that runs afterward.

      * ``managed``  the full automated run: ``n_max`` articles, all personas.
      * ``express``  a quick small batch: ``express_n`` articles by default.
      * ``manual``   operator-driven: the operator's ``n`` / ``persona_ids``, else
                     the full ``n_max``.

    An explicit ``n`` overrides the mode default in every mode; the result is always
    clamped to ``[0, n_max]`` so the fan-out bound can never be exceeded by a
    trigger."""
    if mode not in RUN_MODES:
        raise ValueError(f"invalid run mode {mode!r}; must be one of {RUN_MODES}")
    default_n = express_n if mode == "express" else n_max
    resolved_n = default_n if n is None else n
    resolved_n = max(0, min(resolved_n, n_max))
    ids = tuple(persona_ids) if persona_ids else None
    return RunScope(n=resolved_n, persona_ids=ids, generate_images=images)


def _select_personas(store: PersonaStore, persona_ids: tuple[str, ...] | None) -> list[Persona]:
    """The personas this run may assign to: a requested subset (skipping any that do
    not exist), or all of them. Order is the store's natural order, so a run is
    reproducible."""
    if persona_ids is None:
        return store.list()
    by_id = {p.id: p for p in store.list()}
    return [by_id[pid] for pid in persona_ids if pid in by_id]


def _budget_factory(settings: Settings):
    """Mint a FRESH per-article budget. The budget is per-article (not per-run): the
    fan-out gives each journalist its own token + wall-clock ceiling."""

    def make() -> ArticleBudget:
        return ArticleBudget(
            token_budget=settings.per_article_token_budget,
            wall_clock_s=settings.per_article_wall_clock_s,
        )

    return make


def _publish_ready(dispatch, deps: RunDeps) -> list[PublishOutcome]:
    """The COLLECT -> PUBLISH tail: publish every article that finalized to ``ready``.
    Dropped articles (budget exhausted, finalize failed) have no body and are skipped.

    By default the run's ready articles publish TOGETHER in one atomic batch
    (``settings.publish_batch``); turning the flag off falls back to one POST per
    article. Either way the per-assignment bookkeeping is identical: a publish that
    fails (a 403, a transport fault, an atomic-batch rejection) keeps the persisted body
    and marks the assignment ``publish_failed`` with the publish code, so the failure is
    OBSERVABLE (over GET /runs/{id}) rather than indistinguishable from publish-pending,
    and the article stays re-publishable. WITHIN one run a publish is exactly-once via
    the content-derived key persisted at finalize (a crash after finalize replays the
    stored body to the same key, or resends the same per-item batch key). Across a
    FROM-SCRATCH re-run, new assignment ids mint new keys, so cross-run exactly-once
    rests on the platform's content-hash dedup and is best-effort (a temp-0.9 redraft is
    not byte-identical), not the persisted key."""
    guard = deps.lock if deps.lock is not None else nullcontext()

    # Re-fetch each ready row: the pipeline persisted content_hash + idempotency_key at
    # finalize, and the publish path reads them off the assignment. finalize guarantees
    # the row exists, so a missing row is a should-not-happen guard (skipped).
    ready: list[tuple[str, object]] = []  # (assignment_id, article)
    pairs: list[tuple[object, object]] = []  # (Assignment row, article) for the publisher
    for outcome in dispatch.outcomes:
        if outcome.status != "ready" or outcome.article is None:
            continue
        with guard:
            row = deps.store.get_assignment(outcome.assignment_id)
        if row is None:
            continue
        ready.append((outcome.assignment_id, outcome.article))
        pairs.append((row, outcome.article))

    if deps.settings.publish_batch:
        results = publish_batch_assignments(
            pairs,
            store=deps.store,
            base_url=deps.publish_base_url,
            token=deps.operator_token,
            coverage=deps.coverage_store,
            lock=deps.lock,
        )
    else:
        results = [
            publish_assignment(
                article,
                assignment=row,
                store=deps.store,
                base_url=deps.publish_base_url,
                token=deps.operator_token,
                coverage=deps.coverage_store,
                lock=deps.lock,
            )
            for row, article in pairs
        ]

    published: list[PublishOutcome] = []
    for (assignment_id, _article), result in zip(ready, results):
        if not result.ok:
            # The publisher leaves the row 'ready' on failure; mark it failed so a
            # finalized-but-unpublished article is visible, not a silent green run.
            with guard:
                deps.store.mark_publish_failed(
                    assignment_id, reason=result.code or "publish_failed"
                )
        published.append(
            PublishOutcome(
                assignment_id=assignment_id,
                ok=result.ok,
                published_id=result.id,
                slug=result.slug,
                replayed=result.replayed,
                code=result.code,
            )
        )
    return published


def execute_run(*, run: Run, scope: RunScope, deps: RunDeps) -> RunReport:
    """Run one already-created run to completion and publish its articles.

    The mode is NOT consulted here (only ``run.mode`` is echoed into the report for
    provenance); the scope carries everything that differs between modes. So this is
    the single run-execution path every trigger funnels through.

    Final status: ``done`` when every finalized article published, ``done_with_errors``
    when at least one finalized article failed to publish (those assignments are marked
    ``publish_failed`` and stay re-publishable), or ``failed`` when the run itself
    raised. A dead run is re-run from scratch; WITHIN a run the persisted content-derived
    idempotency key makes the publish replay exactly-once (cross-run safety rests on the
    platform's content-hash dedup, see ``_publish_ready``)."""
    store = deps.store
    settings = deps.settings
    guard = deps.lock if deps.lock is not None else nullcontext()
    try:
        # Materialize the manager's inputs under the lock (store reads), then run the
        # manager on plain lists: the manager makes model calls, which must never hold
        # the shared-connection lock.
        with guard:
            personas = _select_personas(deps.persona_store, scope.persona_ids)
            coverage = deps.coverage_store.recent(limit=settings.coverage_lookback)

        # Nothing to assign (an empty scope or no personas): finish clean WITHOUT
        # running the manager, so a no-op trigger spends zero inference.
        if scope.n <= 0 or not personas:
            with guard:
                store.finish_run(run.id, status="done")
            return RunReport(
                run_id=run.id, mode=run.mode, status="done", manifest=Manifest(assignments=[])
            )

        manifest = run_manager(
            personas=personas,
            coverage=coverage,
            search_news=deps.search_news,
            cfg=deps.roles.manager,
            prompts_dir=deps.prompts_dir,
            n_max=scope.n,
            max_steps=settings.max_manager_steps,
            circuit_breaker=settings.manager_circuit_breaker,
            duplicate_threshold=settings.coverage_duplicate_threshold,
            followup_threshold=settings.coverage_followup_threshold,
        )

        # The art-director image step runs iff it is both available (injected) and
        # enabled for this run: the per-run flag wins, falling back to the global
        # settings default. Resolving it here (not in the pipeline) keeps execute_run
        # the single gate and the pipeline trigger-blind about the on/off decision.
        images_enabled = (
            scope.generate_images
            if scope.generate_images is not None
            else settings.auto_generate_image
        )
        illustrate = deps.illustrate if (images_enabled and deps.illustrate is not None) else None

        dispatch = dispatch_run(
            run_id=run.id,
            manifest=manifest,
            persona_index={p.id: p for p in personas},
            store=store,
            make_ledger=deps.make_ledger,
            drafter_cfg=deps.roles.drafter,
            evaluator_cfg=deps.roles.evaluator,
            finalize_cfg=deps.roles.finalize,
            prompts_dir=deps.prompts_dir,
            budget_factory=_budget_factory(settings),
            max_sweeps=settings.max_sweeps,
            concurrency=settings.fanout_concurrency,
            lock=deps.lock,
            illustrate=illustrate,
        )

        published = _publish_ready(dispatch, deps)

        # A finalized article that failed to publish makes the run done_with_errors
        # (its assignment is marked publish_failed), so a green "done" never hides a
        # silently unpublished article.
        status = "done" if all(p.ok for p in published) else "done_with_errors"
        with guard:
            store.finish_run(run.id, status=status)
        return RunReport(
            run_id=run.id,
            mode=run.mode,
            status=status,
            manifest=manifest,
            outcomes=dispatch.outcomes,
            published=published,
        )
    except Exception:
        # Best-effort cleanup: record the run as failed, but never let the cleanup
        # write supplant the original failure or leave a zombie 'running' row (mirrors
        # the per-article drop path in dispatch).
        try:
            with guard:
                store.finish_run(run.id, status="failed")
        except Exception:
            pass
        raise


def start_run(
    mode: str,
    *,
    deps: RunDeps,
    n: int | None = None,
    persona_ids: list[str] | None = None,
    images: bool | None = None,
) -> tuple[Run, RunScope]:
    """Resolve scope and create the run record WITHOUT executing it. The HTTP surface
    uses this to return a run id immediately (202 + poll), then runs ``execute_run``
    in the background."""
    scope = plan_run(
        mode,
        n_max=deps.settings.n_max,
        express_n=deps.settings.express_n,
        n=n,
        persona_ids=persona_ids,
        images=images,
    )
    guard = deps.lock if deps.lock is not None else nullcontext()
    with guard:
        run = deps.store.create_run(mode=mode, n_requested=scope.n)
    return run, scope


def run_now(
    mode: str,
    *,
    deps: RunDeps,
    n: int | None = None,
    persona_ids: list[str] | None = None,
    images: bool | None = None,
) -> RunReport:
    """Synchronous run: create the record and execute it on this thread. The
    automation entry point (Step 10) and the three mode wrappers below all funnel
    through here."""
    run, scope = start_run(mode, deps=deps, n=n, persona_ids=persona_ids, images=images)
    return execute_run(run=run, scope=scope, deps=deps)


def run_manual(*, deps: RunDeps, n: int | None = None, persona_ids: list[str] | None = None,
               images: bool | None = None) -> RunReport:
    """Entry point: an operator-driven run (explicit ``n`` / ``persona_ids``)."""
    return run_now("manual", deps=deps, n=n, persona_ids=persona_ids, images=images)


def run_express(*, deps: RunDeps, n: int | None = None, persona_ids: list[str] | None = None,
                images: bool | None = None) -> RunReport:
    """Entry point: a quick small batch."""
    return run_now("express", deps=deps, n=n, persona_ids=persona_ids, images=images)


def run_managed(*, deps: RunDeps, n: int | None = None, persona_ids: list[str] | None = None,
                images: bool | None = None) -> RunReport:
    """Entry point: the full automated run."""
    return run_now("managed", deps=deps, n=n, persona_ids=persona_ids, images=images)
