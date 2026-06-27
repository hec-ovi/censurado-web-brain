"""The deterministic fan-out dispatch, driven against the shared fake.

These pin the Step 6 build-plan contracts for the dispatch half:

  (c) no cross-persona context bleed: each journalist runs in its own isolated
      context, so one persona's identity never appears in another's prompts;

plus: the dispatch persists the manifest's assignments and collects one outcome per
assignment (in manifest order, not completion order); one article failing is
isolated to that article and never crashes the run; the shared SQLite connection is
serialized under the lock; and an empty manifest is a no-op, not an error.

Publishing is Step 7, so the dispatch stops at ``ready`` and never POSTs.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from newsroom.config import load_settings
from newsroom.db import open_db
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager import dispatch_run
from newsroom.manager.types import AssignmentSpec, Manifest
from newsroom.personas import Persona, PersonaStore
from newsroom.pipeline import ArticleBudget
from newsroom.research.ledger import Ledger
from newsroom.runs import RunStore


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="x", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"],
    )


def _prompts_dir():
    return load_settings().prompts_dir


def _big_budget() -> ArticleBudget:
    return ArticleBudget(token_budget=10_000_000, wall_clock_s=1e9, clock=lambda: 0.0)


def _make_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="grounding", url="https://src.test/a", snippet="a source")
    return led


def _finalize_ok(title: str = "T", body: str = "B") -> str:
    return json.dumps({"title": title, "body": body, "topics": []})


class _Env:
    def __init__(self, fake, personas: list[Persona]):
        self.conn = open_db(":memory:", check_same_thread=False)
        self.personas = PersonaStore(self.conn)
        self.store = RunStore(self.conn)
        self.index = {}
        for p in personas:
            stored = self.personas.create(p)
            self.index[stored.id] = stored
        self.run = self.store.create_run(mode="managed", n_requested=len(personas))
        # drafter == evaluator endpoint -> rules-degraded evaluator (no eval model call).
        self.drafter = _cfg(fake, "drafter-model")
        self.evaluator = self.drafter
        self.finalize = _cfg(fake, "finalize-model")

    def dispatch(self, manifest, *, concurrency=1, lock=None, make_ledger=None):
        return dispatch_run(
            run_id=self.run.id, manifest=manifest, persona_index=self.index, store=self.store,
            make_ledger=make_ledger or _make_ledger, drafter_cfg=self.drafter, evaluator_cfg=self.evaluator,
            finalize_cfg=self.finalize, prompts_dir=_prompts_dir(), budget_factory=_big_budget,
            max_sweeps=4, concurrency=concurrency, lock=lock,
        )


# ----- (c) no cross-persona context bleed -----


def test_no_cross_persona_context_bleed(fake):
    # Two journalists with deliberately disjoint identity markers. Run sequentially
    # (concurrency 1) so the scripted queue is deterministic: all of A's calls, then B's.
    ada = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover quantum chips.", style="dry")
    bruno = Persona(id="bruno", display_name="Bruno", beat="world", who_i_am="I cover world summits.", style="wry")
    env = _Env(fake, [ada, bruno])
    manifest = Manifest(assignments=[
        AssignmentSpec(persona_id="ada", section="tech", angle="cover a tech story"),
        AssignmentSpec(persona_id="bruno", section="world", angle="cover a world story"),
    ])
    # A's pipeline (outline, draft, enrich, finalize), then B's. Rules-degraded eval,
    # clean bodies -> no eval call and no fact-check revise.
    for body in ("ada outline", "ada draft", "ada enriched", "ada respin 1", "ada respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok("Ada Title", "Ada body"))
    for body in ("bruno outline", "bruno draft", "bruno enriched", "bruno respin 1", "bruno respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok("Bruno Title", "Bruno body"))

    result = env.dispatch(manifest, concurrency=1)

    assert [o.status for o in result.outcomes] == ["ready", "ready"]
    bodies = [json.dumps(r["body"]) for r in fake.state.chat_requests if r["status"] < 300]
    # The load-bearing assertion: no single request ever carries BOTH personas.
    for b in bodies:
        assert not ("quantum chips" in b and "world summits" in b)
    # And each persona's identity DID reach exactly its own draft (so the test is real).
    assert any("quantum chips" in b and "world summits" not in b for b in bodies)
    assert any("world summits" in b and "quantum chips" not in b for b in bodies)


def test_no_cross_persona_context_bleed_under_concurrency(fake):
    # The same isolation must survive PARALLEL execution (the real point of contract
    # c). Default fake responses make the per-request assertion order-independent, so
    # the global scripted queue's interleaving under concurrency=2 does not matter:
    # whatever the interleaving, no single request may carry both personas.
    ada = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover quantum chips.", style="dry")
    bruno = Persona(id="bruno", display_name="Bruno", beat="world", who_i_am="I cover world summits.", style="wry")
    env = _Env(fake, [ada, bruno])
    manifest = Manifest(assignments=[
        AssignmentSpec(persona_id="ada", section="tech", angle="a tech story"),
        AssignmentSpec(persona_id="bruno", section="world", angle="a world story"),
    ])

    env.dispatch(manifest, concurrency=2, lock=threading.Lock())

    bodies = [json.dumps(r["body"]) for r in fake.state.chat_requests if r["status"] < 300]
    for b in bodies:
        assert not ("quantum chips" in b and "world summits" in b)
    assert any("quantum chips" in b for b in bodies)  # both journalists actually ran
    assert any("world summits" in b for b in bodies)


# ----- persist + collect, stop at ready, never POST -----


def test_dispatch_persists_assignments_and_collects_ready_outcomes(fake):
    ada = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="tech", style="dry")
    env = _Env(fake, [ada])
    manifest = Manifest(assignments=[AssignmentSpec(persona_id="ada", section="tech", angle="a story")])
    for body in ("outline", "draft", "enriched", "respin 1", "respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    result = env.dispatch(manifest)

    assert len(result.assignments) == 1 and len(result.outcomes) == 1
    assert result.outcomes[0].status == "ready"
    rows = env.store.list_assignments(run_id=env.run.id)
    assert [r.status for r in rows] == ["ready"]
    assert rows[0].final_body == "B"  # finalized body persisted before any publish
    assert fake.state.publish_requests == []  # publishing is Step 7; dispatch never POSTs


def test_outcomes_are_in_manifest_order_not_completion_order(fake):
    ada = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="tech", style="dry")
    bea = Persona(id="bea", display_name="Bea", beat="world", who_i_am="world", style="dry")
    env = _Env(fake, [ada, bea])
    manifest = Manifest(assignments=[
        AssignmentSpec(persona_id="ada", section="tech", angle="a"),
        AssignmentSpec(persona_id="bea", section="world", angle="b"),
    ])

    # Force completion order to be the REVERSE of manifest order: the first
    # assignment (ada) sleeps at its first pipeline step, so the second (bea)
    # finishes first. The result must still come back in manifest order, which only
    # holds if collection is keyed by submission index, not by who finished first.
    def staggered(assignment, spec, persona, budget):
        if assignment.persona_id == "ada":
            time.sleep(0.2)
        return _make_ledger(assignment, spec, persona, budget)

    result = env.dispatch(manifest, concurrency=2, lock=threading.Lock(), make_ledger=staggered)

    assert [o.assignment_id for o in result.outcomes] == [a.id for a in result.assignments]
    assert [r.persona_id for r in result.assignments] == ["ada", "bea"]


def test_dispatch_persists_entities_onto_the_assignment_row(fake):
    # The spec's entities must land on the persisted assignment row: the row is what
    # carries them to the publish step (the coverage write). This pins the middle link
    # of the de-dup chain (spec -> row).
    ada = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="tech", style="dry")
    env = _Env(fake, [ada])
    manifest = Manifest(assignments=[
        AssignmentSpec(persona_id="ada", section="tech", angle="a story",
                       entities=["Javier Milei", "Casa Rosada"]),
    ])
    for body in ("outline", "draft", "enriched", "respin 1", "respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    result = env.dispatch(manifest)

    row = env.store.get_assignment(result.assignments[0].id)
    assert row.entities == ["Javier Milei", "Casa Rosada"]


# ----- per-article failure isolation -----


def test_one_finalize_failure_does_not_crash_the_run(fake):
    ada = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="tech", style="dry")
    bea = Persona(id="bea", display_name="Bea", beat="world", who_i_am="world", style="dry")
    env = _Env(fake, [ada, bea])
    manifest = Manifest(assignments=[
        AssignmentSpec(persona_id="ada", section="tech", angle="a"),
        AssignmentSpec(persona_id="bea", section="world", angle="b"),
    ])
    # A finalizes cleanly; B's finalize is invalid on both the attempt and its retry.
    for body in ("a outline", "a draft", "a enriched", "a respin 1", "a respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok("A", "A body"))
    for body in ("b outline", "b draft", "b enriched", "b respin 1", "b respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(json.dumps({"title": ""}))
    fake.state.script_chat(json.dumps({"title": ""}))

    result = env.dispatch(manifest, concurrency=1)

    by_id = {o.assignment_id: o for o in result.outcomes}
    a_id, b_id = result.assignments[0].id, result.assignments[1].id
    assert by_id[a_id].status == "ready"
    assert by_id[b_id].status == "dropped" and by_id[b_id].stop_reason == "finalize_failed"
    assert fake.state.publish_requests == []


# ----- the shared connection is serialized under the lock -----


def test_store_writes_go_through_the_lock(fake):
    class _CountingLock:
        def __init__(self):
            self.enters = 0
            self.exits = 0
            self._inner = threading.Lock()

        def __enter__(self):
            self.enters += 1
            self._inner.acquire()
            return self

        def __exit__(self, *exc):
            self._inner.release()
            self.exits += 1
            return False

    ada = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="tech", style="dry")
    env = _Env(fake, [ada])
    manifest = Manifest(assignments=[AssignmentSpec(persona_id="ada", section="tech", angle="a")])
    for body in ("outline", "draft", "enriched", "respin 1", "respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    lock = _CountingLock()
    result = env.dispatch(manifest, lock=lock)

    assert result.outcomes[0].status == "ready"
    # create_assignment (dispatch) + mark_drafting + finalize_assignment (pipeline).
    assert lock.enters >= 3
    assert lock.enters == lock.exits  # every acquire released


# ----- research and the pipeline share ONE per-article budget -----


def test_research_spend_charges_the_same_per_article_budget(fake):
    # The budget minted per article is shared between research (make_ledger) and the
    # pipeline: if research exhausts it, the pipeline starts already over budget and
    # drops the article before drafting. This proves one ceiling over their SUM (A.8),
    # not a separate budget per phase.
    ada = Persona(id="ada", display_name="Ada", beat="tech", who_i_am="tech", style="dry")
    env = _Env(fake, [ada])
    manifest = Manifest(assignments=[AssignmentSpec(persona_id="ada", section="tech", angle="a story")])

    def greedy_research(_assignment, _spec, _persona, budget):
        budget.debit_tokens(10_000_000)  # research alone spends the whole article budget
        return _make_ledger(_assignment, _spec, _persona, budget)

    result = env.dispatch(manifest, make_ledger=greedy_research)

    assert result.outcomes[0].status == "dropped"
    assert result.outcomes[0].stop_reason == "budget_exhausted"
    assert result.outcomes[0].drafts == 0  # never drafted: research spent the ceiling
    assert fake.state.chat_requests == []  # no pipeline model call happened at all


# ----- empty manifest is a no-op -----


def test_empty_manifest_is_a_no_op(fake):
    env = _Env(fake, [])
    result = env.dispatch(Manifest(assignments=[]))
    assert result.assignments == [] and result.outcomes == []
    assert [r for r in fake.state.chat_requests] == []  # nothing ran
