"""The per-article pipeline end to end, driven against the shared fake.

Both the drafter and the evaluator hit the same fake, so scripting is a single
ordered queue of responses. The evaluator is given a DIFFERENT model name than the
drafter, so its endpoint is distinct and it runs model-driven (scriptable PASS /
REVISE verdicts); the rules-degraded path gives them the SAME endpoint instead.

The three contract tests from the build plan:
  (a) PASS on sweep 2  -> exactly 2 drafts
  (b) never PASS       -> exactly MAX_SWEEPS drafts, then finalize (publish-as-is)
  (c) budget exhausted mid-draft -> assignment dropped(budget_exhausted), NO POST,
      and the (complete, untruncated) draft is never published
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from newsroom.config import load_settings
from newsroom.contracts.hashing import content_hash, idempotency_key
from newsroom.db import open_db
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.personas import Persona, PersonaStore
from newsroom.pipeline import ArticleBudget, run_article_pipeline
from newsroom.research.ledger import Ledger
from newsroom.runs import RunStore


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="x", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"],
    )


def _prompts_dir():
    return load_settings().prompts_dir


@dataclass
class Env:
    store: RunStore
    persona: Persona
    assignment: object
    ledger: Ledger
    drafter: ProviderConfig
    evaluator: ProviderConfig
    finalize: ProviderConfig


def _env(fake) -> Env:
    conn = open_db(":memory:")
    personas = PersonaStore(conn)
    store = RunStore(conn)
    persona = personas.create(Persona(
        display_name="Ada Reporter", beat="tech", who_i_am="I cover chips.",
        style="dry and precise", few_shots_pos=["a crisp lede"], few_shots_neg=["hype"],
    ))
    run = store.create_run(mode="managed", n_requested=1)
    assignment = store.create_assignment(
        run_id=run.id, persona_id=persona.id, section="tech", angle="cover the new chip",
    )
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="the chip shipped", url="https://src.test/a", snippet="it shipped")
    return Env(
        store=store, persona=persona, assignment=assignment, ledger=led,
        drafter=_cfg(fake, "drafter-model"), evaluator=_cfg(fake, "evaluator-model"),
        finalize=_cfg(fake, "finalize-model"),
    )


def _run(env: Env, *, budget: ArticleBudget, max_sweeps: int = 4, evaluator=None):
    return run_article_pipeline(
        assignment=env.assignment, persona=env.persona, ledger=env.ledger, store=env.store,
        budget=budget, drafter_cfg=env.drafter, evaluator_cfg=evaluator or env.evaluator,
        finalize_cfg=env.finalize, prompts_dir=_prompts_dir(), max_sweeps=max_sweeps,
    )


def _big_budget() -> ArticleBudget:
    return ArticleBudget(token_budget=10_000_000, wall_clock_s=1e9, clock=lambda: 0.0)


def _revise(failing):
    return json.dumps({"verdict": "REVISE", "feedback": "more work", "failing_sections": failing})


# ----- (a) PASS on sweep 2 -> exactly 2 drafts -----


def test_pass_on_second_sweep_runs_exactly_two_drafts(fake):
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(_revise(["lede"]))
    fake.state.script_chat("draft two")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat(json.dumps({"title": "Final Title", "body": "FINAL BODY", "topics": ["t"]}))

    out = _run(env, budget=_big_budget())

    assert out.status == "ready"
    assert out.stop_reason == "pass"
    assert out.drafts == 2
    assert out.article.title == "Final Title"
    assert out.article.body == "FINAL BODY"
    assert out.article.author == "ada-reporter"  # persona id, not model-chosen
    assert out.article.section == "tech"
    # exactly: outline + (draft,eval) x2 + enrich + finalize == 7 model calls, no extra fact-check call
    assert len(fake.state.chat_requests) == 7

    # The assignment row is finalized (body persisted before any publish, B.0).
    row = env.store.get_assignment(env.assignment.id)
    assert row.status == "ready"
    assert row.final_body == "FINAL BODY"
    expected_hash = content_hash("Final Title", "FINAL BODY", "ada-reporter", "tech")
    assert row.content_hash == expected_hash
    assert row.idempotency_key == idempotency_key(env.assignment.id, expected_hash)
    assert row.ledger_digest == env.ledger.digest()


# ----- (b) never PASS -> exactly MAX_SWEEPS drafts -----


def test_never_pass_stops_at_max_sweeps(fake):
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    # Distinct failing-section sets each sweep, so the no-progress detector never fires
    # and the loop runs the full MAX_SWEEPS=3.
    for i, sweep in enumerate(["a", "b", "c"]):
        fake.state.script_chat(f"draft {i}")
        fake.state.script_chat(_revise([sweep]))
    fake.state.script_chat("enriched body")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    out = _run(env, budget=_big_budget(), max_sweeps=3)

    assert out.drafts == 3  # EXACTLY max_sweeps, asserted exactly
    assert out.stop_reason == "max_sweeps"
    assert out.status == "ready"  # publish-as-is with the best draft
    # outline + (draft,eval) x3 + enrich + finalize == 9
    assert len(fake.state.chat_requests) == 9


def test_repeated_empty_failing_set_runs_to_max_sweeps_not_stalled(fake):
    # A REVISE with no named sections twice running is too ambiguous to early-stop:
    # it is deliberately NOT a stall, so max_sweeps bounds it instead.
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    for i in range(2):
        fake.state.script_chat(f"draft {i}")
        fake.state.script_chat(json.dumps({"verdict": "REVISE", "failing_sections": []}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    out = _run(env, budget=_big_budget(), max_sweeps=2)

    assert out.drafts == 2
    assert out.stop_reason == "max_sweeps"  # NOT "stalled"


def test_store_writes_go_through_the_lock_when_given(fake):
    # The brain serializes its one shared connection under a lock; the pipeline must
    # hold it around every store write (mark_drafting + finalize_assignment here).
    class _CountingLock:
        def __init__(self):
            self.enters = 0
            self.exits = 0

        def __enter__(self):
            self.enters += 1
            return self

        def __exit__(self, *exc):
            self.exits += 1
            return False

    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    lock = _CountingLock()
    out = run_article_pipeline(
        assignment=env.assignment, persona=env.persona, ledger=env.ledger, store=env.store,
        budget=_big_budget(), drafter_cfg=env.drafter, evaluator_cfg=env.evaluator,
        finalize_cfg=env.finalize, prompts_dir=_prompts_dir(), max_sweeps=4, lock=lock,
    )
    assert out.status == "ready"
    assert lock.enters >= 2  # mark_drafting + finalize_assignment
    assert lock.enters == lock.exits  # every acquire released


def test_identical_failing_set_twice_stops_as_stalled(fake):
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft 0")
    fake.state.script_chat(_revise(["lede"]))
    fake.state.script_chat("draft 1")
    fake.state.script_chat(_revise(["lede"]))  # identical set -> stall after this eval
    fake.state.script_chat("enriched body")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    out = _run(env, budget=_big_budget(), max_sweeps=4)

    assert out.drafts == 2
    assert out.stop_reason == "stalled"
    assert out.status == "ready"


# ----- (c) budget exhaustion mid-draft -> drop, NO POST, no truncated body -----


def test_budget_exhaustion_after_draft_drops_without_publishing(fake):
    env = _env(fake)
    fake.state.script_chat("OUTLINE")  # default usage 0
    # The (complete) draft reports usage that blows the small token budget.
    fake.state.script_chat("a COMPLETE, untruncated draft body", usage={"total_tokens": 500})

    budget = ArticleBudget(token_budget=100, wall_clock_s=1e9, clock=lambda: 0.0)
    out = _run(env, budget=budget, max_sweeps=4)

    assert out.status == "dropped"
    assert out.stop_reason == "budget_exhausted"
    assert out.drafts == 1
    assert out.article is None  # nothing to publish

    row = env.store.get_assignment(env.assignment.id)
    assert row.status == "dropped"
    assert row.drop_reason == "budget_exhausted"
    assert row.final_body is None  # the body was never persisted for publish
    assert row.ledger_digest == env.ledger.digest()  # kept for audit

    # NO POST happened: the publish seam was never touched.
    assert fake.state.publish_requests == []
    # Only outline + the one draft were generated; evaluation never ran.
    assert len(fake.state.chat_requests) == 2


# ----- finalize failure is isolated to the article (drop, never crash) -----


def test_finalize_failure_drops_the_article_without_publishing(fake):
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    # Both finalize attempts (original + the one retry) return an invalid payload.
    fake.state.script_chat(json.dumps({"title": ""}))
    fake.state.script_chat(json.dumps({"title": ""}))

    out = _run(env, budget=_big_budget())

    assert out.status == "dropped"
    assert out.stop_reason == "finalize_failed"
    assert out.article is None
    assert fake.state.publish_requests == []  # nothing was published
    row = env.store.get_assignment(env.assignment.id)
    assert row.status == "dropped" and row.drop_reason == "finalize_failed"
    assert row.final_body is None


# ----- rules-degraded evaluator (shared endpoint) -----


def test_rules_degraded_evaluator_passes_a_grounded_draft(fake):
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("Grounded prose citing https://src.test/a, clean.")
    fake.state.script_chat("Enriched, still cites https://src.test/a.")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    # evaluator shares the drafter's endpoint -> rules-grounded check, no eval model call.
    out = _run(env, budget=_big_budget(), evaluator=env.drafter)

    assert out.drafts == 1
    assert out.stop_reason == "pass"
    assert out.status == "ready"
    assert out.evaluations[0].mode == "rules"
    # outline + draft + enrich + finalize == 4 (no evaluator call, no fact-check call)
    assert len(fake.state.chat_requests) == 4
