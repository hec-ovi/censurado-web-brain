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
from newsroom.editorial import PromptStore, StyleStore, style_for_draft, style_for_eval
from newsroom.manager import dispatch_run, plan_batch, run_manager
from newsroom.manager.coverage import CoverageStore, recent_coverage_text
from newsroom.manager.dispatch import LedgerBuilder
from newsroom.manager.manager import NewsSearch
from newsroom.manager.preflight import ResolvedRoles
from newsroom.manager.types import AssignmentSpec, Manifest
from newsroom.personas import Persona, PersonaStore
from newsroom.pipeline import ArticleBudget, ArticleOutcome
from newsroom.pipeline.context import EditorialContext, _no_ownership
from newsroom.publish import publish_assignment, publish_batch_assignments
from newsroom.research.ledger import Ledger
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
    "start_direct",
    "execute_direct",
    "run_direct",
    "start_batch",
    "execute_batch",
    "run_batch",
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
    # The active house style guide source (operator-editable, over the shared
    # connection). None disables style injection (an unconfigured newsroom).
    style_store: "StyleStore | None" = None
    # The versioned prompt library (operator-editable, over the shared connection): the
    # source of the active prompt bodies a run threads as ``overrides`` into every
    # load_prompt so an edited stage template takes effect. None means no DB overrides,
    # so every stage reads its on-disk ``.md`` file (the B1 fallback).
    prompt_store: "PromptStore | None" = None
    # The seed-from-URL fetch seam (url -> extracted text) for the direct-from-link
    # mode (``execute_direct``). None means direct mode has no primary source to seed
    # from (it falls back to corroboration only); a test injects an in-process double.
    fetch: "Callable[[str], str] | None" = None
    # The batch sweep's per-author topic-discovery factory: ``(persona, *, freshness) ->
    # NewsSearch`` scoped to that author's OWN sources (see ``deps._author_discovery``).
    # None disables the batch path (a managed run never needs it); a test injects a double.
    discover_news: "Callable[..., NewsSearch] | None" = None
    # The cross-source corroboration resolver (registrable domain -> ownership-group
    # label, "" when unknown), built ONCE from the portal registry in build_run_deps so
    # the corroboration gate stays a pure function. Defaults to "no co-ownership known",
    # so an injected RunDeps / test double that omits it still constructs and the gate
    # falls back to per-domain sentinels.
    ownership_of: "Callable[[str], str]" = _no_ownership


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


def _resolve_prompt_overrides(deps: RunDeps) -> dict[str, str]:
    """The active prompt bodies as a ``{key: body}`` override map, resolved ONCE at run
    start (under the shared connection lock, since the store holds none). Threaded as an
    explicit argument through the manager, dispatch, and the per-article pipeline so the
    journalists running in the ThreadPoolExecutor workers see the operator's edits -- a
    ContextVar would NOT cross the pool boundary. An absent store, or a key with no active
    version, simply yields no override, so ``load_prompt`` reads the on-disk file."""
    if deps.prompt_store is None:
        return {}
    guard = deps.lock if deps.lock is not None else nullcontext()
    with guard:
        return deps.prompt_store.active_overrides()


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


def _dispatch_publish_finish(
    run: Run,
    *,
    manifest: Manifest,
    personas: list[Persona],
    coverage,
    style_guide,
    scope: RunScope,
    deps: RunDeps,
    prompt_overrides: dict[str, str],
) -> RunReport:
    """The shared run tail: gate the art-director image step, fan out via ``dispatch_run``
    (the SOLE fan-out point), publish the ready articles, and finish the run.

    A managed run (``execute_run``) and a batch (``execute_batch``) compute their manifest
    differently, one global manager vs the manager fanned once per author, then hand it
    here so the dispatch -> publish -> finish path is byte-for-byte identical. The caller
    owns the surrounding try/except (mark the run ``failed`` and re-raise); this helper is
    the happy path only."""
    store = deps.store
    settings = deps.settings
    guard = deps.lock if deps.lock is not None else nullcontext()

    # The art-director image step runs iff it is both available (injected) and enabled
    # for this run: the per-run flag wins, falling back to the global settings default.
    # Resolving it here (not in the pipeline) keeps the runner the single gate and the
    # pipeline trigger-blind about the on/off decision.
    images_enabled = (
        scope.generate_images
        if scope.generate_images is not None
        else settings.auto_generate_image
    )
    illustrate = deps.illustrate if (images_enabled and deps.illustrate is not None) else None

    # The operator's editorial inputs threaded into every article: the active house
    # style (rendered for drafter and evaluator) and a digest of recent coverage so
    # the writer does not repeat published stories. Empty fields render the prompt
    # tokens away, so an unconfigured newsroom behaves exactly as before.
    editorial = EditorialContext(
        house_style_draft=style_for_draft(style_guide),
        house_style_eval=style_for_eval(style_guide),
        style_lexicon=(style_guide.lexicon if style_guide is not None else {}),
        recent_coverage=recent_coverage_text(coverage),
        related_coverage=coverage,  # structured, so the widget step can emit a related card
        min_independent_sources=(style_guide.sourcing.get("min_sources", 0) if style_guide is not None else 0),
        ownership_of=deps.ownership_of,
    )

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
        overrides=prompt_overrides,
        budget_factory=_budget_factory(settings),
        max_sweeps=settings.max_sweeps,
        concurrency=settings.fanout_concurrency,
        lock=deps.lock,
        illustrate=illustrate,
        editorial=editorial,
    )

    published = _publish_ready(dispatch, deps)

    # A finalized article that failed to publish makes the run done_with_errors (its
    # assignment is marked publish_failed), so a green "done" never hides a silently
    # unpublished article.
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
            style_guide = deps.style_store.active() if deps.style_store is not None else None

        # Nothing to assign (an empty scope or no personas): finish clean WITHOUT
        # running the manager, so a no-op trigger spends zero inference.
        if scope.n <= 0 or not personas:
            with guard:
                store.finish_run(run.id, status="done")
            return RunReport(
                run_id=run.id, mode=run.mode, status="done", manifest=Manifest(assignments=[])
            )

        # Resolve the operator's active prompt edits ONCE, then thread them to every stage.
        prompt_overrides = _resolve_prompt_overrides(deps)

        manifest = run_manager(
            personas=personas,
            coverage=coverage,
            search_news=deps.search_news,
            cfg=deps.roles.manager,
            prompts_dir=deps.prompts_dir,
            overrides=prompt_overrides,
            n_max=scope.n,
            max_steps=settings.max_manager_steps,
            circuit_breaker=settings.manager_circuit_breaker,
            duplicate_threshold=settings.coverage_duplicate_threshold,
            followup_threshold=settings.coverage_followup_threshold,
        )

        return _dispatch_publish_finish(
            run,
            manifest=manifest,
            personas=personas,
            coverage=coverage,
            style_guide=style_guide,
            scope=scope,
            deps=deps,
            prompt_overrides=prompt_overrides,
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


# ----- mode 3: direct-from-link (bypasses the manager) -----


def _direct_instruction(brief: str | None, focus: str | None) -> str:
    """Fold the operator's free-text brief and optional focus into ONE instruction the
    journalist writes from. The brief is the open command ("cover the budget vote"); the
    focus narrows it ("the impact on pensions"). Either may be empty; the result is the
    non-empty parts joined, so a brief-only request reads exactly as the brief and a
    focus-only request still carries the angle."""
    parts: list[str] = []
    if brief and brief.strip():
        parts.append(brief.strip())
    if focus and focus.strip():
        parts.append(f"Focus on: {focus.strip()}")
    return "\n".join(parts)


def _direct_ledger_builder(deps: RunDeps, links: list[str], instruction: str):
    """A ``make_ledger`` for the ONE direct assignment: seed the ledger from EACH operator
    link (fetch + add it as a primary grounded source), then research OUTWARD from the
    brief with the author's normal scoped research loop (the persona's own portals),
    merging its rows in. Every fetch and the outward research DEBIT the same per-article
    budget so the direct path is bounded exactly like every other article.

    The operator vouched for these links, so the brief is the starting point, not a single
    fixed source: the agent reads the 0..N links the operator handed it and then keeps
    digging on the instruction. A failed/empty fetch on any link is not fatal (that link
    contributes nothing); with no links at all the article grounds purely on the outward
    research. If everything comes back empty the ledger is empty and the evaluator's
    grounding gate drops it. Nothing here raises into the pipeline."""

    def make_ledger(assignment, spec, persona, budget) -> Ledger:
        ledger = Ledger()
        for url in links:
            if budget.exhausted():
                break
            text = deps.fetch(url) if deps.fetch is not None else ""
            if text:
                # The fetch counts toward the per-article bound: charge a rough token cost
                # for the page so a huge document cannot ground for free (a real debit, no
                # output cap, it only bounds the INPUT we paid to read).
                budget.debit_tokens(len(text) // 4)
                ledger.add(claim=(instruction or spec.angle or "primary source"), url=url, snippet=text)
        # Outward research: the author's normal research loop on the brief/angle, scoped to
        # its own source pool, charging the SAME budget. Skipped once the budget is spent
        # (the fetches may have exhausted it), and isolated so a research fault never sinks
        # the directly-requested article.
        if not budget.exhausted():
            try:
                corroboration = deps.make_ledger(assignment, spec, persona, budget)
                for row in corroboration:
                    ledger.add(claim=row.claim, url=row.url, snippet=row.snippet)
            except Exception:
                pass
        return ledger

    return make_ledger


def start_direct(*, deps: RunDeps) -> Run:
    """Create the ``direct`` run record WITHOUT executing it, so the HTTP surface can
    return a run id immediately (202 + poll) and run ``execute_direct`` in the
    background, mirroring ``start_run``."""
    guard = deps.lock if deps.lock is not None else nullcontext()
    with guard:
        return deps.store.create_run(mode="direct", n_requested=1)


def execute_direct(
    *, run: Run, links: list[str], persona_id: str, deps: RunDeps, brief: str | None = None,
    focus: str | None = None, images: bool | None = None,
) -> RunReport:
    """Run mode 3: hand ONE persona a BRIEF (a free-text instruction, 0..N links the
    operator vouched for, and an optional focus) and get an article, BYPASSING the manager.

    There is no triage and no manager call. The single assignment is funneled through
    the EXACT same per-article pipeline + publish tail as a managed run by reusing
    ``dispatch_run`` with a one-item manifest, so ``dispatch_run`` remains the sole
    fan-out point (a single direct article simply has nothing to fan out). The ledger is
    seeded from each link and then enriched by researching OUTWARD from the brief, instead
    of from a manager-driven research topic.

    The cross-source corroboration GATE is FORCED OFF here (``min_independent_sources=0``)
    regardless of the house style's ``sourcing.min_sources``: the operator vouched for the
    source and asked for this specific piece, so a single-source brief is a deliberate,
    legitimate request, not a quality-floor violation. That is the deliberate divergence
    from a managed run, where the gate stays armed as the autonomous quality floor. The
    article still passes every OTHER normal gate (evaluate, fact-check, finalize) and a
    grounding-empty result is still dropped, never published unbacked."""
    store = deps.store
    settings = deps.settings
    guard = deps.lock if deps.lock is not None else nullcontext()
    try:
        with guard:
            persona = deps.persona_store.get(persona_id)
            coverage = deps.coverage_store.recent(limit=settings.coverage_lookback)
            style_guide = deps.style_store.active() if deps.style_store is not None else None

        # An unknown persona (the HTTP/CLI entry validates first; this is the safety
        # net): finish clean with nothing assigned rather than crash the background run.
        if persona is None:
            with guard:
                store.finish_run(run.id, status="done")
            return RunReport(
                run_id=run.id, mode=run.mode, status="done", manifest=Manifest(assignments=[])
            )

        # The operator's active prompt edits, resolved once and threaded into the pipeline
        # (the direct path bypasses the manager, but the per-article stages are identical).
        prompt_overrides = _resolve_prompt_overrides(deps)

        instruction = _direct_instruction(brief, focus)
        spec = AssignmentSpec(
            persona_id=persona.id,
            section=persona.beat,  # authoritative: a persona writes their own beat
            angle=(instruction or "Write an article about these sources."),
            headline=(brief or ""),
        )
        manifest = Manifest(assignments=[spec])

        images_enabled = images if images is not None else settings.auto_generate_image
        illustrate = deps.illustrate if (images_enabled and deps.illustrate is not None) else None

        editorial = EditorialContext(
            house_style_draft=style_for_draft(style_guide),
            house_style_eval=style_for_eval(style_guide),
            style_lexicon=(style_guide.lexicon if style_guide is not None else {}),
            recent_coverage=recent_coverage_text(coverage),
            related_coverage=coverage,  # structured, so the widget step can emit a related card
            # The operator vouched for the source: corroboration is OFF for the direct
            # path, never read from the style guide. Managed runs keep it armed (see
            # execute_run). The agent still researches outward, it just is not DROPPED for
            # resting on a single independent source.
            min_independent_sources=0,
            ownership_of=deps.ownership_of,
        )

        dispatch = dispatch_run(
            run_id=run.id,
            manifest=manifest,
            persona_index={persona.id: persona},
            store=store,
            make_ledger=_direct_ledger_builder(deps, links, instruction),
            drafter_cfg=deps.roles.drafter,
            evaluator_cfg=deps.roles.evaluator,
            finalize_cfg=deps.roles.finalize,
            prompts_dir=deps.prompts_dir,
            overrides=prompt_overrides,
            budget_factory=_budget_factory(settings),
            max_sweeps=settings.max_sweeps,
            concurrency=1,  # one article: nothing to fan out
            lock=deps.lock,
            illustrate=illustrate,
            editorial=editorial,
        )

        published = _publish_ready(dispatch, deps)
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
        try:
            with guard:
                store.finish_run(run.id, status="failed")
        except Exception:
            pass
        raise


def run_direct(
    *, deps: RunDeps, links: list[str], persona_id: str, brief: str | None = None,
    focus: str | None = None, images: bool | None = None,
) -> RunReport:
    """Synchronous direct-from-brief run: create the record and execute it on this
    thread (the CLI path; the HTTP surface uses ``start_direct`` + a background
    ``execute_direct`` instead). ``links`` is the 0..N sources the operator vouched for,
    ``brief``/``focus`` the free-text instruction the agent researches outward from."""
    run = start_direct(deps=deps)
    return execute_direct(
        run=run, links=links, persona_id=persona_id, deps=deps, brief=brief, focus=focus,
        images=images,
    )


# ----- batch sweep: the manager fanned once per author over per-author sources -----


def start_batch(
    *, deps: RunDeps, persona_ids: list[str] | None = None, images: bool | None = None,
) -> tuple[Run, RunScope]:
    """Create the ``batch`` run record WITHOUT executing it (the async/HTTP path), mirroring
    ``start_run``. A batch has no single fan-out ``n``: each author contributes up to
    ``settings.batch_per_author`` and the optional overall cap is an ``execute_batch``
    argument, so the scope here carries only the persona subset and the image override (its
    ``n`` is unused by the batch path)."""
    ids = tuple(persona_ids) if persona_ids else None
    scope = RunScope(n=0, persona_ids=ids, generate_images=images)
    guard = deps.lock if deps.lock is not None else nullcontext()
    with guard:
        run = deps.store.create_run(mode="batch", n_requested=None)
    return run, scope


def execute_batch(
    *, run: Run, scope: RunScope, deps: RunDeps, timeframe: str | None = None,
    max_total: int | None = None,
) -> RunReport:
    """Run mode 4: a BATCH sweep. The manager is fanned ONCE PER AUTHOR over each author's
    own source-scoped, time-filtered discovery search (``plan_batch``), instead of one
    global ``run_manager`` over the open web. Everything AFTER the manifest, the image gate,
    the per-article fan-out, publish, and finish, is the IDENTICAL shared tail a managed run
    uses (``_dispatch_publish_finish``), so a batch article is built and published exactly
    like any other, and ``dispatch_run`` stays the sole fan-out point.

    ``timeframe`` overrides ``settings.batch_timeframe`` (the discovery freshness window);
    ``max_total`` caps the whole batch (``None`` leaves the natural ``batch_per_author *
    len(personas)`` bound). Requires ``deps.discover_news`` (the per-author discovery
    factory); a managed run never sets it, so the batch path validates it explicitly rather
    than failing deep in the loop."""
    store = deps.store
    settings = deps.settings
    guard = deps.lock if deps.lock is not None else nullcontext()
    try:
        if deps.discover_news is None:
            # A wiring error (a managed run's deps reused for a batch): fail the run
            # record cleanly via the except below rather than leaving a zombie 'running'.
            raise ValueError(
                "execute_batch requires deps.discover_news (the per-author discovery factory)"
            )
        with guard:
            personas = _select_personas(deps.persona_store, scope.persona_ids)
            coverage = deps.coverage_store.recent(limit=settings.coverage_lookback)
            style_guide = deps.style_store.active() if deps.style_store is not None else None

        # No personas (an empty subset): finish clean WITHOUT running any manager, so a
        # no-op batch spends zero inference (mirrors execute_run's empty-scope short-circuit).
        if not personas:
            with guard:
                store.finish_run(run.id, status="done")
            return RunReport(
                run_id=run.id, mode=run.mode, status="done", manifest=Manifest(assignments=[])
            )

        prompt_overrides = _resolve_prompt_overrides(deps)

        manifest = plan_batch(
            personas=personas,
            coverage=coverage,
            discover=deps.discover_news,
            cfg=deps.roles.manager,
            prompts_dir=deps.prompts_dir,
            overrides=prompt_overrides,
            per_author=settings.batch_per_author,
            timeframe_freshness=timeframe or settings.batch_timeframe,
            max_steps=settings.max_manager_steps,
            circuit_breaker=settings.manager_circuit_breaker,
            duplicate_threshold=settings.coverage_duplicate_threshold,
            followup_threshold=settings.coverage_followup_threshold,
            max_total=max_total,
        )

        return _dispatch_publish_finish(
            run,
            manifest=manifest,
            personas=personas,
            coverage=coverage,
            style_guide=style_guide,
            scope=scope,
            deps=deps,
            prompt_overrides=prompt_overrides,
        )
    except Exception:
        try:
            with guard:
                store.finish_run(run.id, status="failed")
        except Exception:
            pass
        raise


def run_batch(
    *, deps: RunDeps, persona_ids: list[str] | None = None, images: bool | None = None,
    timeframe: str | None = None, max_total: int | None = None,
) -> RunReport:
    """Synchronous batch run: create the record and execute it on this thread (the CLI
    path; the HTTP surface uses ``start_batch`` + a background ``execute_batch`` instead).
    ``persona_ids`` narrows the desks (default: all), ``timeframe`` the discovery window,
    ``max_total`` the overall article ceiling."""
    run, scope = start_batch(deps=deps, persona_ids=persona_ids, images=images)
    return execute_batch(run=run, scope=scope, deps=deps, timeframe=timeframe, max_total=max_total)
