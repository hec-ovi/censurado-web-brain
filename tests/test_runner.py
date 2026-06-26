"""The run orchestrator (Step 8): the trigger-blind run-execution path and its three
entry points, driven through to real side effects against the shared fake.

The build-plan contracts for Step 8:

  * invoke all three entry points (``manual`` | ``express`` | ``managed``) and assert
    they take the IDENTICAL run-execution path (``execute_run``), differing only in
    the resolved scope;
  * assert the brain is TRIGGER-BLIND: no module under ``newsroom/`` names cron, n8n,
    or windmill, so the automation layer can change without touching the brain;

plus the mode->scope resolution (``plan_run``), a full managed run end to end
(manager triage -> one journalist -> publish, with a coverage row recorded), a clean
no-op when there are no personas, and the failed-run bookkeeping.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

import newsroom
from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager.coverage import CoverageStore
from newsroom.manager.preflight import ResolvedRoles
from newsroom.manager.types import Candidate
from newsroom.personas import Persona, PersonaStore
from newsroom.publish import publish_assignment
from newsroom.research.ledger import Ledger
from newsroom.runner import run as run_mod
from newsroom.runner import (
    RunDeps,
    execute_run,
    plan_run,
    run_express,
    run_managed,
    run_manual,
    start_run,
)
from newsroom.runs import RunStore


# ----- builders -----


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="x", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"]
    )


def _roles(fake) -> ResolvedRoles:
    # drafter == evaluator endpoint -> the rules-grounded evaluator degrade (no eval
    # model call), which keeps the scripted chat queue short and deterministic.
    drafter = _cfg(fake, "drafter-model")
    return ResolvedRoles(
        drafter=drafter, evaluator=drafter, finalize=_cfg(fake, "finalize-model"),
        manager=drafter, evaluator_distinct=False,
    )


def _ready_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    """A make_ledger double: a non-empty grounded ledger (so the rules evaluator can
    PASS), without running the research loop or touching the network. Ignores the
    per-article budget it is handed (real research debits it; see test_dispatch)."""
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="grounding", url="https://src.test/a", snippet="a source")
    return led


def _no_search(_query: str) -> list[Candidate]:
    return []


def _assign(persona_id: str, headline: str = "A fresh story", angle: str = "Cover it") -> str:
    return json.dumps(
        {"action": "assign", "assignments": [
            {"persona_id": persona_id, "headline": headline, "angle": angle, "triage": "new"}
        ]}
    )


def _finalize_ok(title: str = "Title", body: str = "Final body.") -> str:
    return json.dumps({"title": title, "body": body, "topics": ["chips"]})


def _settings(fake, tmp_path, **over) -> Settings:
    base = dict(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
        publish_base_url=fake.base_url,
        operator_token="op-token",
    )
    base.update(over)
    return Settings(**base)


def _deps(fake, settings, *, personas, search_news=None, make_ledger=None,
          coverage_store=None, lock=None) -> RunDeps:
    conn = open_db(":memory:", check_same_thread=False)
    persona_store = PersonaStore(conn)
    for p in personas:
        persona_store.create(p)
    return RunDeps(
        store=RunStore(conn),
        persona_store=persona_store,
        coverage_store=coverage_store if coverage_store is not None else CoverageStore(conn),
        roles=_roles(fake),
        search_news=search_news or _no_search,
        make_ledger=make_ledger or _ready_ledger,
        publish_base_url=fake.base_url,
        operator_token="op-token",
        prompts_dir=settings.prompts_dir,
        settings=settings,
        lock=lock if lock is not None else threading.Lock(),
    )


def _assign_many(specs: list[tuple[str, str]]) -> str:
    return json.dumps(
        {"action": "assign", "assignments": [
            {"persona_id": pid, "headline": h, "angle": "cover it", "triage": "new"} for pid, h in specs
        ]}
    )


def _ada() -> Persona:
    return Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover chips.", style="dry")


def _bea() -> Persona:
    return Persona(id="bea", display_name="Bea", beat="world", who_i_am="I cover summits.", style="wry")


def _cleo() -> Persona:
    return Persona(id="cleo", display_name="Cleo", beat="politics", who_i_am="I cover policy.", style="flat")


# ----- plan_run: the trigger surface (mode -> scope) -----


def test_plan_run_resolves_each_mode_to_its_scope():
    assert plan_run("managed", n_max=4, express_n=2).n == 4  # full run
    assert plan_run("express", n_max=4, express_n=2).n == 2  # small batch
    assert plan_run("manual", n_max=4, express_n=2).n == 4   # operator default = full
    # An explicit n overrides the default in any mode, clamped to [0, n_max].
    assert plan_run("manual", n_max=4, express_n=2, n=3).n == 3
    assert plan_run("manual", n_max=4, express_n=2, n=99).n == 4  # never above n_max
    assert plan_run("express", n_max=1, express_n=2).n == 1       # express clamped down
    assert plan_run("managed", n_max=4, express_n=2, n=0).n == 0
    # A persona subset rides along on the scope.
    scope = plan_run("manual", n_max=4, express_n=2, persona_ids=["ada", "bea"])
    assert scope.persona_ids == ("ada", "bea")
    assert plan_run("managed", n_max=4, express_n=2).persona_ids is None


def test_plan_run_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="invalid run mode"):
        plan_run("turbo", n_max=4, express_n=2)


# ----- contract: all three entry points share ONE execution path -----


def test_three_entry_points_funnel_through_execute_run(fake, tmp_path, monkeypatch):
    # Spy on execute_run: each wrapper must reach it exactly once, with the mode
    # stamped on the run and the mode-resolved scope. This is the "identical path"
    # contract made concrete: the wrappers differ only in the scope they resolve.
    settings = _settings(fake, tmp_path, n_max=4, express_n=2)
    deps = _deps(fake, settings, personas=[_ada()])

    calls = []

    def _spy(*, run, scope, deps):
        calls.append((run.mode, scope.n))
        return "report"

    monkeypatch.setattr(run_mod, "execute_run", _spy)

    assert run_manual(deps=deps, n=3) == "report"
    assert run_express(deps=deps) == "report"
    assert run_managed(deps=deps) == "report"

    assert calls == [("manual", 3), ("express", 2), ("managed", 4)]
    # And each wrapper actually created a run record with its mode (the real side
    # effect of start_run), so the spy did not skip the shared front half.
    modes = sorted(r.mode for r in [deps.store.get_run(c) for c in _all_run_ids(deps)])
    assert modes == ["express", "managed", "manual"]


def _all_run_ids(deps) -> list[str]:
    rows = deps.store._conn.execute("SELECT id FROM runs").fetchall()
    return [r["id"] for r in rows]


def test_run_execution_path_does_not_branch_on_the_mode():
    # The structural proof behind "identical path": the run-execution path never
    # compares against a mode literal. The mode is consulted ONLY in plan_run (the
    # trigger surface) and named by the three entry-point wrappers; EVERY OTHER
    # function in run.py is the shared path and must be mode-blind. The guard scans the
    # whole module (not a hand-listed subset) so a new helper cannot silently fall
    # outside it -- this is what the earlier version missed (_budget_factory).
    # Match the QUOTED mode literals, so the identifier ``express_n`` (a scope knob, not
    # a branch) does not count as a mode comparison.
    literals = ('"manual"', "'manual'", '"express"', "'express'", '"managed"', "'managed'")
    entry_points = {run_mod.plan_run, run_mod.run_manual, run_mod.run_express, run_mod.run_managed}
    path_fns = [
        obj
        for obj in vars(run_mod).values()
        if inspect.isfunction(obj) and obj.__module__ == run_mod.__name__ and obj not in entry_points
    ]
    # Sanity: the core path and the helper the old guard missed are actually scanned.
    assert run_mod.execute_run in path_fns
    assert run_mod._budget_factory in path_fns
    for fn in path_fns:
        src = inspect.getsource(fn)
        for lit in literals:
            assert lit not in src, f"{fn.__name__} references the mode literal {lit}"
    # Positive control: the mode literals DO exist in run.py (in plan_run / the entry
    # points / RUN_MODES), so the guard above is not vacuously passing.
    module_src = inspect.getsource(run_mod)
    assert all(lit in module_src for lit in ('"manual"', '"express"', '"managed"'))


# ----- contract: the brain is trigger-blind (no automation-layer knowledge) -----


def test_newsroom_names_no_scheduler():
    # The brain must not know how it is triggered: the mode enum is the entire trigger
    # surface, so no module may name a specific automation layer. Docs may discuss
    # cron/Windmill; the shippable brain code may not.
    forbidden = re.compile(r"\b(cron|crontab|n8n|windmill)\b", re.IGNORECASE)
    root = Path(newsroom.__file__).resolve().parent
    offenders = []
    for path in root.rglob("*.py"):
        if forbidden.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"automation-layer names leaked into the brain: {offenders}"


# ----- a full managed run, end to end against the fake -----


def test_managed_run_drafts_and_publishes_one_article(fake, tmp_path):
    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, personas=[_ada()])
    # The manager assigns one story to ada; then her pipeline runs (outline, draft,
    # enrich, finalize -- rules-degraded eval and a clean body add no calls).
    fake.state.script_chat(_assign("ada", headline="Chips ship early"))
    for body in ("an outline", "a clean draft", "an enriched body"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok("Chips Ship Early", "The chips shipped."))

    report = run_managed(deps=deps)

    assert report.status == "done"
    assert report.mode == "managed"
    assert len(report.manifest.assignments) == 1
    assert [o.status for o in report.outcomes] == ["ready"]
    # Exactly one article was published, and the platform recorded it. The default
    # publish path is the atomic batch (one item here).
    assert [p.ok for p in report.published] == [True]
    assert len(fake.state.batch_requests) == 1 and len(fake.state.batch_requests[0]["items"]) == 1
    published_id = report.published[0].published_id
    assert published_id

    # The run record is closed, the assignment is published, and a coverage row was
    # written so the NEXT run's manager will not republish this story.
    run_row = deps.store.get_run(report.run_id)
    assert run_row.status == "done" and run_row.finished_at
    assignment = deps.store.list_assignments(run_id=report.run_id)[0]
    assert assignment.status == "published" and assignment.published_id == published_id
    coverage = deps.coverage_store.recent(limit=10)
    assert len(coverage) == 1
    assert coverage[0].headline == "Chips Ship Early"
    assert coverage[0].section == "tech"


def test_a_rerun_publish_of_a_finalized_assignment_is_idempotent(fake, tmp_path):
    # The runner-level re-run / crash-replay guarantee (deferred from Step 8's review):
    # the content-derived idempotency key minted at finalize and PERSISTED on the
    # assignment is what makes a second publish of the same finalized article
    # exactly-once. A re-run replays to the IDENTICAL key, so the platform returns the
    # idempotent 200 with the same id rather than minting a second article, and the
    # already-published guard records no second coverage row.
    #
    # This pins publish_batch off so BOTH the first publish and the manual replay below
    # go through the single endpoint, the path whose per-request key/content-hash this
    # test asserts. The batch path's own replay idempotency is covered in
    # test_publish_batch.py.
    settings = _settings(fake, tmp_path, publish_batch=False)
    deps = _deps(fake, settings, personas=[_ada()])
    fake.state.script_chat(_assign("ada", headline="Chips ship early"))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok("Chips Ship Early", "The chips shipped."))

    report = run_managed(deps=deps)

    # First publish CREATED the article (201), minting exactly one distinct article.
    assert [p.replayed for p in report.published] == [False]
    first_id = report.published[0].published_id
    assert len(fake.state.publish_requests) == 1
    assert len(fake.state.by_hash) == 1  # one distinct content hash -> one article

    outcome = report.outcomes[0]
    row = deps.store.get_assignment(outcome.assignment_id)
    assert row.status == "published" and row.idempotency_key  # finalize persisted the key

    # Re-publish the SAME finalized assignment through the runner's publish tail (the
    # crash-after-finalize replay path). The row carries the persisted key + content
    # hash, so the POST replays under the identical key.
    result = publish_assignment(
        outcome.article,
        assignment=row,
        store=deps.store,
        base_url=fake.base_url,
        token="op-token",
        coverage=deps.coverage_store,
        lock=deps.lock,
    )

    assert result.ok and result.replayed and result.id == first_id  # SAME article, 200 replay
    assert len(fake.state.by_hash) == 1  # nothing new minted on the platform
    assert len(deps.coverage_store.recent(limit=10)) == 1  # already-published -> no double record
    # The platform saw a second POST, but under the same key and content hash: a replay.
    assert len(fake.state.publish_requests) == 2
    assert {r["content_hash"] for r in fake.state.publish_requests} == {row.content_hash}
    assert {r["key"] for r in fake.state.publish_requests} == {row.idempotency_key}


def test_publish_payload_carries_newsroom_provenance(fake, tmp_path):
    # The published article's author is the persona, its section is the persona's beat,
    # and the metadata.newsroom provenance namespace rides along on the wire.
    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, personas=[_ada()])
    fake.state.script_chat(_assign("ada"))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    run_managed(deps=deps)

    sent = fake.state.batch_requests[0]["items"][0]
    assert sent["author"] == "ada"
    assert sent["section"] == "tech"
    assert sent["metadata"]["newsroom"]["run_id"]  # provenance stamped at finalize


def test_run_with_no_personas_is_a_clean_noop(fake, tmp_path):
    # No personas -> nobody to assign -> execute_run short-circuits BEFORE the manager:
    # a done run that drafted and published nothing, with ZERO model calls.
    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, personas=[])

    report = run_managed(deps=deps)

    assert report.status == "done"
    assert report.manifest.assignments == []
    assert report.outcomes == [] and report.published == []
    assert fake.state.publish_requests == []
    assert fake.state.chat_requests == []  # short-circuited: the manager never ran
    assert deps.store.get_run(report.run_id).status == "done"


def test_run_with_n_zero_is_a_clean_noop(fake, tmp_path):
    # n=0 is reachable from the trigger surface (POST /runs {"n": 0}); like the
    # no-personas case it short-circuits before the manager, spending no inference.
    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, personas=[_ada()])

    report = run_manual(deps=deps, n=0)

    assert report.status == "done"
    assert report.manifest.assignments == []
    assert report.published == []
    assert fake.state.chat_requests == []  # n=0 spends zero inference
    assert deps.store.get_run(report.run_id).status == "done"


def test_a_failing_run_is_recorded_as_failed(fake, tmp_path):
    # If the run raises before the manifest (here: reading coverage blows up), the run
    # record is marked failed and the error propagates -- a dead run is re-run, never
    # silently lost.
    class _BoomCoverage:
        def recent(self, *, limit):
            raise RuntimeError("coverage store is down")

    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, personas=[_ada()], coverage_store=_BoomCoverage())

    run, scope = start_run("managed", deps=deps)
    with pytest.raises(RuntimeError, match="coverage store is down"):
        execute_run(run=run, scope=scope, deps=deps)

    failed = deps.store.get_run(run.id)
    assert failed.status == "failed"
    assert failed.finished_at  # the run was closed, not left a zombie 'running'


def test_dropped_article_is_not_published(fake, tmp_path):
    # An article the pipeline drops (here: finalize invalid on both the attempt and its
    # one retry) finalizes to "dropped", so the publish tail skips it: nothing is POSTed
    # and no coverage row is written.
    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, personas=[_ada()])
    fake.state.script_chat(_assign("ada"))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(json.dumps({"title": ""}))  # invalid finalize
    fake.state.script_chat(json.dumps({"title": ""}))  # invalid retry

    report = run_managed(deps=deps)

    assert report.status == "done"  # the run itself is fine; one article dropped
    assert [o.status for o in report.outcomes] == ["dropped"]
    assert report.published == []
    assert fake.state.publish_requests == []
    assert deps.coverage_store.recent(limit=10) == []


def test_publish_failure_marks_done_with_errors_and_keeps_the_article(fake, tmp_path):
    # A finalized article whose publish 403s must NOT vanish into a green run: the run
    # is done_with_errors, the assignment is publish_failed (body kept, re-publishable),
    # and no coverage row is written.
    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, personas=[_ada()])
    deps.operator_token = "noscope-token"  # the fake 403s insufficient_scope on publish
    fake.state.script_chat(_assign("ada"))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    report = run_managed(deps=deps)

    assert report.status == "done_with_errors"
    assert [o.status for o in report.outcomes] == ["ready"]  # the pipeline DID finalize it
    assert [p.ok for p in report.published] == [False]
    assert report.published[0].code == "insufficient_scope"
    row = deps.store.list_assignments(run_id=report.run_id)[0]
    assert row.status == "publish_failed" and row.drop_reason == "insufficient_scope"
    assert row.final_body  # body kept, so the article stays re-publishable
    assert deps.coverage_store.recent(limit=10) == []  # no coverage on a failed publish
    assert deps.store.get_run(report.run_id).status == "done_with_errors"


def test_batch_validation_failure_marks_every_article_done_with_errors(fake, tmp_path):
    # The run-level atomic-422 path: with the default batch on and a key that may not
    # author as the personas, the platform rejects the WHOLE batch (422), so NOTHING is
    # written and EVERY finalized assignment is marked publish_failed -> done_with_errors.
    settings = _settings(fake, tmp_path)  # publish_batch on (default)
    deps = _deps(fake, settings, personas=[_ada(), _bea()])
    deps.operator_token = "agent-a-token"  # write-only, locked to author "agent-a"
    fake.state.script_chat(_assign_many([("ada", "A tech story"), ("bea", "A world story")]))
    for _ in range(2):
        for body in ("outline", "draft", "enriched"):
            fake.state.script_chat(body)
        fake.state.script_chat(_finalize_ok("Distinct " + str(_), "A body."))

    report = run_managed(deps=deps)

    assert report.status == "done_with_errors"
    assert [o.status for o in report.outcomes] == ["ready", "ready"]  # both finalized
    assert all(p.ok is False and p.code == "author_mismatch" for p in report.published)
    rows = deps.store.list_assignments(run_id=report.run_id)
    assert all(r.status == "publish_failed" and r.drop_reason == "author_mismatch" for r in rows)
    assert fake.state.by_hash == {}  # atomic: nothing written
    assert fake.state.batch_requests[0]["status"] == 422
    assert deps.coverage_store.recent(limit=10) == []  # no coverage on a failed batch


def test_per_article_fallback_publish_failure_marks_done_with_errors(fake, tmp_path):
    # The per-article fallback FAILURE path: with batch off, each article publishes via
    # POST /articles; a 403 marks that assignment publish_failed and the run
    # done_with_errors, and the batch endpoint is never touched.
    settings = _settings(fake, tmp_path, publish_batch=False)
    deps = _deps(fake, settings, personas=[_ada(), _bea()])
    deps.operator_token = "noscope-token"  # the fake 403s insufficient_scope on publish
    fake.state.script_chat(_assign_many([("ada", "A tech story"), ("bea", "A world story")]))
    for _ in range(2):
        for body in ("outline", "draft", "enriched"):
            fake.state.script_chat(body)
        fake.state.script_chat(_finalize_ok("Fallback " + str(_), "A body."))

    report = run_managed(deps=deps)

    assert report.status == "done_with_errors"
    assert all(p.ok is False and p.code == "insufficient_scope" for p in report.published)
    rows = deps.store.list_assignments(run_id=report.run_id)
    assert all(r.status == "publish_failed" and r.final_body for r in rows)  # kept, re-publishable
    assert fake.state.batch_requests == []  # the batch endpoint was not used
    assert deps.coverage_store.recent(limit=10) == []


def test_run_decollides_a_duplicate_slug_and_stays_green(fake, tmp_path):
    # Two journalists cover the same headline, so both articles derive the same slug.
    # Without de-collision the platform's within-batch unique-slug rule would 422 the
    # whole atomic batch; the run instead de-collides the second and finishes done.
    settings = _settings(fake, tmp_path)  # publish_batch on (default)
    deps = _deps(fake, settings, personas=[_ada(), _bea()])
    fake.state.script_chat(_assign_many([("ada", "Scoop one"), ("bea", "Scoop two")]))
    # Both pipelines run (concurrency 1, in manifest order). Both finalize the SAME title
    # -> same derived slug; distinct bodies -> distinct content hashes (so they do not
    # just dedup, forcing the slug clash the de-collision must resolve).
    for body_text in ("Ada's take on the scoop.", "Bea's different angle on it."):
        for body in ("outline", "draft", "enriched"):
            fake.state.script_chat(body)
        fake.state.script_chat(_finalize_ok("Hot Scoop", body_text))

    report = run_managed(deps=deps)

    assert report.status == "done"  # NOT done_with_errors: the slug clash was de-collided
    assert [p.ok for p in report.published] == [True, True]
    assert len(fake.state.by_hash) == 2  # two distinct articles landed
    sent = fake.state.batch_requests[0]["items"]
    assert sent[1]["slug"] == "hot-scoop-2"  # the second was pinned to a unique slug


def test_manual_run_honors_a_persona_subset(fake, tmp_path):
    # persona_ids narrows the run to a subset; an unknown id is silently skipped, not
    # an error. The manager tries to assign BOTH ada and bea, but the run was scoped to
    # ada (+ a non-existent "ghost"), so only ada is assignable.
    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, personas=[_ada(), _bea()])
    fake.state.script_chat(_assign_many([("ada", "A tech story"), ("bea", "A world story")]))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    report = run_manual(deps=deps, persona_ids=["ada", "ghost"])

    assert report.status == "done"
    assert [a.persona_id for a in report.manifest.assignments] == ["ada"]  # bea excluded, ghost skipped
    assert [p.ok for p in report.published] == [True]


def test_express_caps_the_batch_to_express_n(fake, tmp_path):
    # Express's defining behavior: scope.n = express_n (=2) flows through run_manager's
    # clamp, so even with 3 personas and a manager trying to assign 3, at most 2 are
    # published. n_max=4 is deliberately distinct, so a settings.n_max-vs-scope.n swap
    # would change the observed article count.
    settings = _settings(fake, tmp_path, express_n=2, n_max=4)
    deps = _deps(fake, settings, personas=[_ada(), _bea(), _cleo()])
    fake.state.script_chat(_assign_many([("ada", "A"), ("bea", "B"), ("cleo", "C")]))
    for _ in range(2):  # the two that survive the clamp, in manifest order
        for body in ("outline", "draft", "enriched"):
            fake.state.script_chat(body)
        fake.state.script_chat(_finalize_ok())

    report = run_express(deps=deps)

    assert report.mode == "express"
    assert len(report.manifest.assignments) == 2  # clamped to express_n, not n_max=4
    assert [o.status for o in report.outcomes] == ["ready", "ready"]
    assert [p.ok for p in report.published] == [True, True]
    # Both published together in one atomic batch.
    assert len(fake.state.batch_requests) == 1 and len(fake.state.batch_requests[0]["items"]) == 2


def test_roles_for_settings_reports_distinctness_and_honors_per_role_overrides(fake, tmp_path, monkeypatch):
    from newsroom.runner import roles_for_settings

    settings = _settings(fake, tmp_path)
    base = str(settings.inference_base_url).rstrip("/")
    # Single backend: the evaluator shares the drafter's endpoint -> not distinct.
    assert roles_for_settings(settings).evaluator_distinct is False
    assert roles_for_settings(settings).drafter.base_url == base

    # A per-role evaluator MODEL override diverges the endpoint, reported honestly
    # (computed, not the old hardcoded False).
    monkeypatch.setenv("NEWSROOM_ROLE_EVALUATOR_MODEL", "a-distinct-eval-model")
    assert roles_for_settings(settings).evaluator_distinct is True

    # A per-role BASE_URL override is honored (no longer silently ignored); other roles
    # stay on the brain's configured backend.
    monkeypatch.setenv("NEWSROOM_ROLE_FINALIZE_BASE_URL", "http://other:9000/v1")
    roles = roles_for_settings(settings)
    assert roles.finalize.base_url == "http://other:9000/v1"
    assert roles.drafter.base_url == base


def test_create_app_unifies_the_lock_across_both_surfaces(fake, tmp_path):
    # The shared-connection safety invariant: synthesis and runs serialize on ONE lock.
    # create_app must make app.state.lock IS run_deps.lock, even when an injected
    # RunDeps left lock unset (the dataclass default None) -- never a split where the
    # run path degrades to nullcontext().
    from newsroom.brain import create_app

    settings = _settings(fake, tmp_path)
    # (a) default build path.
    app = create_app(settings=settings)
    assert app.state.lock is not None
    assert app.state.lock is app.state.run_deps.lock
    # (b) injected deps whose lock is None must be back-filled, not split.
    deps = _deps(fake, settings, personas=[_ada()])
    deps.lock = None
    app2 = create_app(settings=settings, store=deps.persona_store, run_deps=deps)
    assert app2.state.run_deps.lock is not None
    assert app2.state.lock is app2.state.run_deps.lock
