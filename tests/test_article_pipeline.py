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


def _env(fake, *, about: str = "", avatar_path: str = "") -> Env:
    conn = open_db(":memory:")
    personas = PersonaStore(conn)
    store = RunStore(conn)
    persona = personas.create(Persona(
        display_name="Ada Reporter", beat="tech", who_i_am="I cover chips.",
        about=about, avatar_path=avatar_path,
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


def _run(env: Env, *, budget: ArticleBudget, max_sweeps: int = 4, evaluator=None, illustrate=None,
         editorial=None):
    return run_article_pipeline(
        assignment=env.assignment, persona=env.persona, ledger=env.ledger, store=env.store,
        budget=budget, drafter_cfg=env.drafter, evaluator_cfg=evaluator or env.evaluator,
        finalize_cfg=env.finalize, prompts_dir=_prompts_dir(), max_sweeps=max_sweeps,
        illustrate=illustrate, editorial=editorial,
    )


def _content(req) -> str:
    """The user prompt text the adapter sent for one recorded chat request."""
    return req["body"]["messages"][0]["content"]


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
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
    fake.state.script_chat(json.dumps({"title": "Final Title", "body": "FINAL BODY", "topics": ["t"]}))

    out = _run(env, budget=_big_budget())

    assert out.status == "ready"
    assert out.stop_reason == "pass"
    assert out.drafts == 2
    assert out.article.title == "Final Title"
    assert out.article.body == "FINAL BODY"
    assert out.article.author == "ada-reporter"  # persona id, not model-chosen
    assert out.article.section == "tech"
    # exactly: outline + (draft,eval) x2 + enrich + respin x2 + finalize == 9 model calls,
    # no extra fact-check call (clean body)
    assert len(fake.state.chat_requests) == 9

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
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    out = _run(env, budget=_big_budget(), max_sweeps=3)

    assert out.drafts == 3  # EXACTLY max_sweeps, asserted exactly
    assert out.stop_reason == "max_sweeps"
    assert out.status == "ready"  # publish-as-is with the best draft
    # outline + (draft,eval) x3 + enrich + respin x2 + finalize == 11
    assert len(fake.state.chat_requests) == 11


def test_repeated_empty_failing_set_runs_to_max_sweeps_not_stalled(fake):
    # A REVISE with no named sections twice running is too ambiguous to early-stop:
    # it is deliberately NOT a stall, so max_sweeps bounds it instead.
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    for i in range(2):
        fake.state.script_chat(f"draft {i}")
        fake.state.script_chat(json.dumps({"verdict": "REVISE", "failing_sections": []}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
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
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
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
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
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
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
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


# ----- art-director image step (best-effort, after finalize) -----


def _pass_run_script(fake):
    """Script a one-sweep PASS run through finalize (shared by the image tests).

    Includes the two voiced self-respin passes that run after enrich/fact-check and
    before finalize (respin_passes defaults to 2)."""
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
    fake.state.script_chat(json.dumps({"title": "Final Title", "body": "FINAL BODY"}))


def test_art_director_stamps_image_metadata_and_persists_url(fake):
    from newsroom.imagery import ImageResult

    env = _env(fake)
    _pass_run_script(fake)

    def illustrate(*, article, persona, ledger, budget):
        return ImageResult(
            url="/media/abc123.png", alt="an illustrated chip", prompt="a screen-print chip",
            seed=7, workflow="flux2_klein", references=["https://src.test/hero.jpg"],
        )

    out = _run(env, budget=_big_budget(), illustrate=illustrate)

    assert out.status == "ready"
    assert out.image_url == "/media/abc123.png"
    # Media rides in metadata (the portal's hero-image contract), NOT a new top-level field.
    assert out.article.metadata["image"] == "/media/abc123.png"
    assert out.article.metadata["image_alt"] == "an illustrated chip"
    prov = out.article.metadata["newsroom"]["image"]
    assert prov["prompt"] == "a screen-print chip" and prov["seed"] == 7
    assert prov["references"] == ["https://src.test/hero.jpg"]

    # The image is OUTSIDE the content hash, so the identity is image-independent: the
    # persisted key matches the no-image hash exactly (idempotency is preserved).
    row = env.store.get_assignment(env.assignment.id)
    assert row.image_url == "/media/abc123.png"
    assert row.image_prompt == "a screen-print chip"  # the brief persisted for audit
    expected_hash = content_hash("Final Title", "FINAL BODY", "ada-reporter", "tech")
    assert row.content_hash == expected_hash
    assert row.idempotency_key == idempotency_key(env.assignment.id, expected_hash)


def test_no_illustrator_publishes_without_an_image(fake):
    env = _env(fake)
    _pass_run_script(fake)

    out = _run(env, budget=_big_budget())  # illustrate=None

    assert out.status == "ready"
    assert out.image_url is None
    assert "image" not in (out.article.metadata or {})
    assert env.store.get_assignment(env.assignment.id).image_url is None


def test_illustrator_failure_degrades_to_no_image_never_drops_the_article(fake):
    env = _env(fake)
    _pass_run_script(fake)

    def boom(*, article, persona, ledger, budget):
        raise RuntimeError("comfyui is down")

    out = _run(env, budget=_big_budget(), illustrate=boom)

    # The finalized article is still published; a missing picture never drops it.
    assert out.status == "ready"
    assert out.image_url is None
    assert "image" not in (out.article.metadata or {})
    row = env.store.get_assignment(env.assignment.id)
    assert row.status == "ready" and row.image_url is None


def test_spent_budget_at_finalize_skips_the_art_director_without_dropping(fake):
    from newsroom.imagery import ImageResult

    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
    # Finalize reports usage that exhausts the small budget, so by the art-director gate
    # the budget is spent: the step is skipped, but the article still finalizes. (The
    # usage carries the full OpenAI shape, which the pydantic-ai finalize seam validates.)
    fake.state.script_chat(
        json.dumps({"title": "T", "body": "B"}),
        usage={"prompt_tokens": 5_000, "completion_tokens": 5_000, "total_tokens": 10_000},
    )

    calls = []

    def spy(*, article, persona, ledger, budget):
        calls.append(1)
        return ImageResult(url="/media/x.png", alt="a", prompt="p", seed=1, workflow="w", references=[])

    budget = ArticleBudget(token_budget=500, wall_clock_s=1e9, clock=lambda: 0.0)
    out = _run(env, budget=budget, illustrate=spy)

    assert out.status == "ready"  # finalized and publishable
    assert calls == []  # the art director was gated off by the exhausted budget
    assert out.image_url is None


# ----- rules-degraded evaluator (shared endpoint) -----


def test_rules_degraded_evaluator_passes_a_grounded_draft(fake):
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("Grounded prose citing https://src.test/a, clean.")
    fake.state.script_chat("Enriched, still cites https://src.test/a.")
    fake.state.script_chat("Respun, still cites https://src.test/a, pass 1.")
    fake.state.script_chat("Respun, still cites https://src.test/a, pass 2.")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    # evaluator shares the drafter's endpoint -> rules-grounded check, no eval model call.
    out = _run(env, budget=_big_budget(), evaluator=env.drafter)

    assert out.drafts == 1
    assert out.stop_reason == "pass"
    assert out.status == "ready"
    assert out.evaluations[0].mode == "rules"
    # outline + draft + enrich + respin x2 + finalize == 6 (no evaluator call, no fact-check call)
    assert len(fake.state.chat_requests) == 6


# ----- author identity stamped into metadata (bylines / author pages / About) -----


def test_author_identity_is_stamped_into_published_metadata(fake):
    # The persona carries a third-person byline bio and an avatar, both of which the
    # portal needs to render a byline and an author page from the publish contract alone.
    env = _env(fake, about="Ada Reporter covers chips for the desk.", avatar_path="/media/ada.png")
    _pass_run_script(fake)

    out = _run(env, budget=_big_budget())  # illustrate=None: author metadata is image-independent

    assert out.status == "ready"
    md = out.article.metadata
    assert md["author_name"] == env.persona.display_name == "Ada Reporter"
    assert md["author_bio"] == env.persona.about == "Ada Reporter covers chips for the desk."
    assert md["author_avatar"] == env.persona.avatar_path == "/media/ada.png"
    # Stamped even with the art director OFF (no image was generated).
    assert "image" not in md

    # metadata is OUTSIDE the content hash, so the author keys do NOT shift the article
    # identity or the persisted idempotency key.
    expected_hash = content_hash("Final Title", "FINAL BODY", "ada-reporter", "tech")
    assert out.content_hash == expected_hash
    assert out.idempotency_key == idempotency_key(env.assignment.id, expected_hash)
    row = env.store.get_assignment(env.assignment.id)
    assert row.content_hash == expected_hash
    assert row.idempotency_key == idempotency_key(env.assignment.id, expected_hash)


def test_author_avatar_omitted_when_avatar_path_is_empty(fake):
    # The usual case: a persona with no avatar yet. author_name/bio are always present;
    # author_avatar is omitted entirely so the portal never renders a broken image.
    env = _env(fake, about="Ada Reporter covers chips for the desk.", avatar_path="")
    _pass_run_script(fake)

    out = _run(env, budget=_big_budget())

    assert out.status == "ready"
    md = out.article.metadata
    assert md["author_name"] == "Ada Reporter"
    assert md["author_bio"] == "Ada Reporter covers chips for the desk."
    assert "author_avatar" not in md


def test_author_identity_does_not_change_the_content_hash(fake):
    # Prove the author metadata is hash-neutral by construction: the hash of an article
    # WITH author metadata equals the hash of one without it (it is over title/body/
    # author/section only). Run two pipelines that differ ONLY in persona identity and
    # assert their content hashes are identical.
    env_with = _env(fake, about="A full bio.", avatar_path="/media/a.png")
    _pass_run_script(fake)
    out_with = _run(env_with, budget=_big_budget())

    env_without = _env(fake, about="", avatar_path="")
    _pass_run_script(fake)
    out_without = _run(env_without, budget=_big_budget())

    assert out_with.article.metadata["author_bio"] == "A full bio."
    assert out_without.article.metadata["author_bio"] == ""
    assert "author_avatar" in out_with.article.metadata
    assert "author_avatar" not in out_without.article.metadata
    # Same title/body/author/section -> identical content hash regardless of author metadata.
    assert out_with.content_hash == out_without.content_hash


def test_author_identity_coexists_with_image_metadata(fake):
    # When the art director DOES run, author keys and image keys both land in metadata;
    # neither clobbers the other, and the content hash stays image- and author-neutral.
    from newsroom.imagery import ImageResult

    env = _env(fake, about="Ada Reporter covers chips for the desk.", avatar_path="/media/ada.png")
    _pass_run_script(fake)

    def illustrate(*, article, persona, ledger, budget):
        return ImageResult(
            url="/media/abc123.png", alt="an illustrated chip", prompt="a screen-print chip",
            seed=7, workflow="flux2_klein", references=[],
        )

    out = _run(env, budget=_big_budget(), illustrate=illustrate)

    md = out.article.metadata
    assert md["author_name"] == "Ada Reporter"
    assert md["author_bio"] == "Ada Reporter covers chips for the desk."
    assert md["author_avatar"] == "/media/ada.png"
    assert md["image"] == "/media/abc123.png"
    assert md["image_alt"] == "an illustrated chip"
    expected_hash = content_hash("Final Title", "FINAL BODY", "ada-reporter", "tech")
    assert out.content_hash == expected_hash


# ----- self-respin: two voiced revision passes before finalize -----


def test_respin_runs_two_voiced_passes_in_the_author_voice(fake):
    # After enrich/fact-check the author re-spins its own article twice (default
    # respin_passes=2), each pass carrying the persona block (voiced, unlike enrich) and
    # the article-so-far. The body fed to finalize is the LAST respin's output.
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat("RESPUN ONCE")
    fake.state.script_chat("RESPUN TWICE")
    fake.state.script_chat(json.dumps({"title": "T", "body": "FINAL"}))

    out = _run(env, budget=_big_budget())

    assert out.status == "ready"
    # outline(0) + draft(1) + eval(2) + enrich(3) + respin(4) + respin(5) + finalize(6)
    assert len(fake.state.chat_requests) == 7
    respin_1 = _content(fake.state.chat_requests[4])
    respin_2 = _content(fake.state.chat_requests[5])
    # Voiced: the persona's who-i-am rides in both respin prompts (enrich is persona-blind).
    assert "I cover chips." in respin_1 and "I cover chips." in respin_2
    # Each pass sees the prior stage's output: pass 1 gets the enriched body, pass 2 the
    # first respin's output.
    assert "enriched body" in respin_1
    assert "RESPUN ONCE" in respin_2
    # The pass number is threaded into the prompt.
    assert "pass 1" in respin_1 and "pass 2" in respin_2


def test_respin_passes_zero_skips_the_respin_stage(fake):
    # respin_passes=0 is an escape hatch (e.g. a fast lane): straight from fact-check to
    # finalize, no respin call.
    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    out = run_article_pipeline(
        assignment=env.assignment, persona=env.persona, ledger=env.ledger, store=env.store,
        budget=_big_budget(), drafter_cfg=env.drafter, evaluator_cfg=env.evaluator,
        finalize_cfg=env.finalize, prompts_dir=_prompts_dir(), max_sweeps=4, respin_passes=0,
    )

    assert out.status == "ready"
    # outline + draft + eval + enrich + finalize == 5 (no respin call)
    assert len(fake.state.chat_requests) == 5


# ----- editorial context: the house style + recent coverage reach the prompts (R8/R9) -----


def test_house_style_and_recent_coverage_reach_the_draft(fake):
    from newsroom.pipeline.context import EditorialContext

    env = _env(fake)
    _pass_run_script(fake)
    ed = EditorialContext(
        house_style_draft="HOUSE STYLE FOR THE DRAFTER",
        house_style_eval="HOUSE STYLE FOR THE EVALUATOR",
        recent_coverage="WHAT WAS ALREADY PUBLISHED",
    )

    out = _run(env, budget=_big_budget(), editorial=ed)

    assert out.status == "ready"
    # chat calls: 0=outline, 1=draft, 2=evaluate, 3=enrich, 4/5=respin, 6=finalize.
    draft_prompt = _content(fake.state.chat_requests[1])
    assert "HOUSE STYLE FOR THE DRAFTER" in draft_prompt
    assert "WHAT WAS ALREADY PUBLISHED" in draft_prompt
    eval_prompt = _content(fake.state.chat_requests[2])
    assert "HOUSE STYLE FOR THE EVALUATOR" in eval_prompt


def test_lexicon_gate_forces_a_revise_inside_the_pipeline(fake):
    # The model evaluator PASSes, but the first draft uses a banned term, so the
    # deterministic gate overrides to REVISE and the pipeline runs a second draft.
    from newsroom.pipeline.context import EditorialContext

    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("Un giro demoledor en los mercados.")  # banned term present
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))  # the model would pass it...
    fake.state.script_chat("Una version sobria del mismo hecho.")  # second draft, clean
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))

    ed = EditorialContext(style_lexicon={"banned_terms": ["demoledor"]})
    out = _run(env, budget=_big_budget(), editorial=ed)

    assert out.status == "ready"
    assert out.drafts == 2  # the gate rejected the first draft despite the model PASS
    assert "lexicon" in out.evaluations[0].failing_sections


# ----- cross-source corroboration gate (chunk 4b): drop BEFORE any draft token -----


def _ownership_resolver():
    """The same dict-backed resolver shape build_run_deps produces from the portals:
    clarin and its co-owned sibling share one ownership group; lanacion is its own."""
    table = {"clarin.com": "grupo-clarin", "tn.com.ar": "grupo-clarin", "lanacion.com.ar": "grupo-la-nacion"}
    return lambda domain: table.get(domain, "")


def _ledger_with(*urls: str) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    for i, url in enumerate(urls):
        led.add(claim=f"claim {i}", url=url, snippet=f"snippet {i}")
    return led


def test_uncorroborated_ledger_is_dropped_before_any_draft(fake):
    # Two co-owned outlets are ONE independent source; with min_independent_sources=2 the
    # assignment is dropped up front. No outline/draft is ever generated (no tokens spent),
    # exactly like the budget-exhausted drop.
    from newsroom.pipeline.context import EditorialContext

    env = _env(fake)
    env.ledger = _ledger_with("https://www.clarin.com/a", "https://tn.com.ar/b")  # both grupo-clarin
    ed = EditorialContext(min_independent_sources=2, ownership_of=_ownership_resolver())

    out = _run(env, budget=_big_budget(), editorial=ed)

    assert out.status == "dropped"
    assert out.stop_reason == "uncorroborated"
    assert out.drafts == 0
    assert out.article is None
    # The drafter/outline model was NEVER called: the gate fired before _outline.
    assert fake.state.chat_requests == []
    assert fake.state.publish_requests == []
    # The assignment row records the drop, with the ledger digest kept for audit.
    row = env.store.get_assignment(env.assignment.id)
    assert row.status == "dropped" and row.drop_reason == "uncorroborated"
    assert row.final_body is None
    assert row.ledger_digest == env.ledger.digest()


def test_corroborated_ledger_is_not_dropped_and_proceeds_to_draft(fake):
    # Two INDEPENDENT outlets (distinct ownership groups) meet min_independent_sources=2,
    # so the gate passes and the pipeline drafts and finalizes exactly as before.
    from newsroom.pipeline.context import EditorialContext

    env = _env(fake)
    env.ledger = _ledger_with("https://clarin.com/a", "https://www.lanacion.com.ar/b")
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))
    ed = EditorialContext(min_independent_sources=2, ownership_of=_ownership_resolver())

    out = _run(env, budget=_big_budget(), editorial=ed)

    assert out.status == "ready"  # NOT dropped for corroboration
    assert out.stop_reason == "pass"
    assert out.drafts == 1
    # outline + draft + eval + enrich + respin x2 + finalize == 7 (eval distinct endpoint, clean body)
    assert len(fake.state.chat_requests) == 7


# ----- inline widgets: a cited tweet + a related card reach the finished article -----


def test_widget_step_emits_cited_tweet_marker_and_snapshot(fake):
    # The journalist's ledger cites a tweet; the widget step captures it (keyless, via the
    # injected fetch) and emits a {{tweet}} marker into the finished body plus a snapshot in
    # metadata. The marker is part of the body, so it lands INSIDE the content hash.
    from newsroom.embeds.fetch import FetchResult

    env = _env(fake)
    env.ledger = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    env.ledger.add(claim="the founder posted", url="https://x.com/jack/status/20", snippet="a tweet")

    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
    fake.state.script_chat(json.dumps(
        {"title": "Final Title", "body": "El fundador habló en https://x.com/jack/status/20 ayer."}
    ))

    def fetch(url):
        if "/status/20" in url:
            return FetchResult(200, {"tweet": {
                "id": "20", "text": "just setting up my twttr",
                "url": "https://twitter.com/jack/status/20", "created_timestamp": 1142974214,
                "author": {"screen_name": "jack", "name": "Jack", "avatar_url": "https://pbs.twimg.com/jack.jpg"},
            }}, True)
        return FetchResult(404, None, False)

    out = run_article_pipeline(
        assignment=env.assignment, persona=env.persona, ledger=env.ledger, store=env.store,
        budget=_big_budget(), drafter_cfg=env.drafter, evaluator_cfg=env.evaluator,
        finalize_cfg=env.finalize, prompts_dir=_prompts_dir(), max_sweeps=4, widget_fetch=fetch,
    )

    assert out.status == "ready"
    assert "{{tweet:20}}" in out.article.body
    snap = out.article.metadata["tweets"][0]
    assert snap["id"] == "20" and snap["text"] == "just setting up my twttr"
    # The marker rides in the body, so the persisted hash is over the marked-up body.
    expected = content_hash("Final Title", out.article.body, "ada-reporter", "tech")
    assert out.content_hash == expected
    assert env.store.get_assignment(env.assignment.id).content_hash == expected


def test_widget_step_emits_related_card_from_editorial_coverage(fake):
    # Prior coverage threaded on the EditorialContext lets the step emit one
    # {{relacionado:slug}} for the best fingerprint match.
    from newsroom.manager.coverage import CoverageItem
    from newsroom.pipeline.context import EditorialContext

    env = _env(fake)
    fake.state.script_chat("OUTLINE")
    fake.state.script_chat("draft one")
    fake.state.script_chat(json.dumps({"verdict": "PASS"}))
    fake.state.script_chat("enriched body")
    fake.state.script_chat("respin pass 1")
    fake.state.script_chat("respin pass 2")
    fake.state.script_chat(json.dumps(
        {"title": "El nuevo chip llega al mercado", "body": "Una nota sobre el nuevo chip.",
         "topics": ["chip", "mercado"]}
    ))

    ed = EditorialContext(related_coverage=[
        CoverageItem(section="tech", headline="El nuevo chip en pruebas",
                     topics=["chip", "mercado"], slug="chip-pruebas-1a2b"),
    ])
    out = _run(env, budget=_big_budget(), editorial=ed)

    assert out.status == "ready"
    assert "{{relacionado:chip-pruebas-1a2b}}" in out.article.body
