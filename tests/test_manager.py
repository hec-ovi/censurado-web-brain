"""The manager's bounded ReAct loop, driven against the shared fake.

Each manager turn is one scripted chat completion returning a JSON action
(``search`` or ``assign``); a ``FakeNews`` double answers the searches. These pin
the build-plan contracts for Step 6:

  (a) the manager clamps ``len(assignments) <= N_MAX``;
  (b) hitting the step cap forces a PARTIAL manifest and never fails the run;

plus the circuit breaker, the rules-first coverage triage (drop duplicates, mark
follow-ups), and the section-authoritative / unknown-persona guards.
"""

from __future__ import annotations

import json
import re

import newsroom.manager.manager as manager_mod
from newsroom.config import load_settings
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager import run_manager
from newsroom.manager.coverage import CoverageItem
from newsroom.manager.types import Candidate, Triage
from newsroom.personas import Persona
from newsroom.pipeline import ArticleBudget


def _cfg(fake, model: str = "manager-model") -> ProviderConfig:
    return ProviderConfig(
        role="manager", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"],
    )


def _prompts_dir():
    return load_settings().prompts_dir


def _persona(pid: str, beat: str) -> Persona:
    return Persona(id=pid, display_name=pid.title(), beat=beat, who_i_am=f"I cover {beat}.",
                   style="dry")


def _search(query: str) -> str:
    return json.dumps({"action": "search", "query": query})


def _assign(assignments) -> str:
    return json.dumps({"action": "assign", "assignments": assignments})


class FakeNews:
    """A recording news-search double: maps a query to candidate stories. Unscripted
    queries get one fresh, distinct candidate (a productive search) unless a constant
    default is supplied (to simulate distinct-but-useless searches)."""

    def __init__(self, default=None):
        self.calls: list[str] = []
        self._scripted: dict[str, list[Candidate]] = {}
        self._default = default

    def script(self, query: str, candidates: list[Candidate]) -> None:
        self._scripted[query] = candidates

    def __call__(self, query: str) -> list[Candidate]:
        self.calls.append(query)
        if query in self._scripted:
            return list(self._scripted[query])
        if self._default is not None:
            return list(self._default)
        slug = re.sub(r"\W+", "-", query.lower()).strip("-")
        return [Candidate(headline=f"Story about {query}", section="tech", url=f"https://news.test/{slug}")]


def _run(fake, news, personas, coverage, **kw):
    return run_manager(
        personas=personas, coverage=coverage, search_news=news, cfg=_cfg(fake),
        prompts_dir=_prompts_dir(), **kw,
    )


# ----- (a) the N_MAX clamp -----


def test_emitted_assignments_clamped_to_n_max(fake):
    personas = [_persona("ada", "tech"), _persona("bea", "world"),
                _persona("cyd", "politics"), _persona("dan", "economics")]
    # The model emits FOUR distinct assignments; n_max=2 clamps to two.
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "chip A", "angle": "chip A"},
        {"persona_id": "bea", "headline": "summit B", "angle": "summit B"},
        {"persona_id": "cyd", "headline": "bill C", "angle": "bill C"},
        {"persona_id": "dan", "headline": "market D", "angle": "market D"},
    ]))
    m = _run(fake, FakeNews(), personas, [], n_max=2, max_steps=4)
    assert m.stop_reason == "emitted"
    assert m.forced is False
    assert len(m.assignments) == 2  # clamped, asserted exactly
    assert {a.persona_id for a in m.assignments} == {"ada", "bea"}  # first two kept


# ----- (b) step-cap hit -> partial manifest, run does not fail -----


def test_step_cap_forces_partial_manifest_without_failing(fake):
    personas = [_persona("ada", "tech"), _persona("bea", "tech")]
    # The model NEVER assigns: it searches with distinct (productive) queries until
    # the step cap. The manager must force a terminal from what it gathered.
    for q in ("tech news one", "tech news two", "tech news three"):
        fake.state.script_chat(_search(q))

    m = _run(fake, FakeNews(), personas, [], n_max=4, max_steps=3, circuit_breaker=5)

    assert m.stop_reason == "max_steps"
    assert m.forced is True
    assert m.steps == 3
    assert m.candidates_considered == 3
    # PARTIAL: it degraded to the stories it found, one per available persona, never failing.
    assert 1 <= len(m.assignments) <= 2
    assert all(a.persona_id in {"ada", "bea"} for a in m.assignments)


# ----- circuit breaker -----


def test_identical_query_trips_the_circuit_breaker(fake):
    personas = [_persona("ada", "tech")]
    for _ in range(8):  # the model is stuck repeating one query
        fake.state.script_chat(_search("same query"))

    m = _run(fake, FakeNews(), personas, [], n_max=4, max_steps=8, circuit_breaker=2)

    assert m.stop_reason == "circuit_breaker"
    assert m.forced is True
    assert m.steps < 8  # it stopped early, well before the step cap


def test_distinct_but_useless_searches_trip_the_breaker_like_a_stall(fake):
    # K DISTINCT queries that each return the SAME already-seen story add no new
    # candidate; the breaker catches that the way research's stall detector does.
    personas = [_persona("ada", "tech")]
    constant = [Candidate(headline="same old story", section="tech", url="https://news.test/dup")]
    news = FakeNews(default=constant)
    for q in ("q1", "q2", "q3", "q4", "q5"):
        fake.state.script_chat(_search(q))

    m = _run(fake, news, personas, [], n_max=4, max_steps=8, circuit_breaker=2)

    assert m.stop_reason == "circuit_breaker"
    assert m.candidates_considered == 1  # only the first search added anything


def test_malformed_actions_trip_the_breaker(fake):
    personas = [_persona("ada", "tech")]
    for _ in range(4):
        fake.state.script_chat("I am not going to give you JSON, sorry.")

    m = _run(fake, FakeNews(), personas, [], n_max=4, max_steps=8, circuit_breaker=3)

    assert m.stop_reason == "circuit_breaker"
    assert m.assignments == []  # nothing gathered, nothing assigned (run still fine)


# ----- coverage triage -----


def test_duplicate_story_is_dropped_by_the_rules_floor(fake):
    personas = [_persona("ada", "tech")]
    coverage = [CoverageItem(section="tech", headline="OpenAI releases GPT-6 to developers",
                             entities=["OpenAI", "GPT-6"], slug="openai-gpt6")]
    # The model tries to assign the SAME story we already published.
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "OpenAI releases GPT-6 to developers",
         "angle": "OpenAI releases GPT-6 to developers", "entities": ["OpenAI", "GPT-6"]},
    ]))

    m = _run(fake, FakeNews(), personas, coverage, n_max=4)

    assert m.assignments == []  # dropped regardless of what the model wanted
    assert m.dropped_duplicates == 1


def test_follow_up_attaches_the_prior_slug_and_brief(fake):
    personas = [_persona("ada", "tech")]
    coverage = [CoverageItem(section="tech", headline="OpenAI releases GPT-6 to developers",
                             entities=["OpenAI", "GPT-6"], slug="openai-gpt6")]
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "GPT-6 pricing draws complaints from startups",
         "angle": "cover the pricing backlash", "entities": ["GPT-6"],
         "triage": "follow_up", "follow_up_slug": "openai-gpt6"},
    ]))

    m = _run(fake, FakeNews(), personas, coverage, n_max=4, followup_threshold=0.2)

    assert len(m.assignments) == 1
    spec = m.assignments[0]
    assert spec.triage is Triage.FOLLOW_UP
    assert spec.follow_up_slug == "openai-gpt6"
    assert "FOLLOW-UP" in spec.angle and "openai-gpt6" in spec.angle


def test_emitted_assignment_carries_entities_for_dedup(fake):
    # The entities the manager triaged on must ride onto the AssignmentSpec. Otherwise
    # they never reach the published article's coverage row and the entity de-dup
    # channel is dead, so a reworded headline about the same event slips the floor next
    # run. This pins the first link of that chain (model verdict -> spec).
    personas = [_persona("ada", "tech")]
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "Milei anuncia recortes", "angle": "el ajuste",
         "entities": ["Javier Milei", "Casa Rosada"]},
    ]))
    m = _run(fake, FakeNews(), personas, [], n_max=4)

    assert len(m.assignments) == 1
    assert m.assignments[0].entities == ["Javier Milei", "Casa Rosada"]


# ----- guards on the emitted assignments -----


def test_section_is_authoritative_from_the_persona_beat(fake):
    personas = [_persona("ada", "tech")]
    # The model puts the wrong section on the assignment; the persona's beat wins.
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "section": "world", "headline": "a chip story", "angle": "a chip story"},
    ]))
    m = _run(fake, FakeNews(), personas, [], n_max=4)
    assert len(m.assignments) == 1
    assert m.assignments[0].section == "tech"  # persona.beat, not the model's "world"


def test_unknown_persona_is_skipped(fake):
    personas = [_persona("ada", "tech")]
    fake.state.script_chat(_assign([
        {"persona_id": "ghost", "headline": "x", "angle": "x"},
        {"persona_id": "ada", "headline": "real chip story", "angle": "real chip story"},
    ]))
    m = _run(fake, FakeNews(), personas, [], n_max=4)
    assert [a.persona_id for a in m.assignments] == ["ada"]  # ghost dropped, ada kept


def test_search_then_assign_is_the_clean_path(fake):
    personas = [_persona("ada", "tech")]
    fake.state.script_chat(_search("today's top tech"))
    fake.state.script_chat(_assign([{"persona_id": "ada", "headline": "a real story", "angle": "a real story"}]))

    news = FakeNews()
    m = _run(fake, news, personas, [], n_max=4)

    assert m.stop_reason == "emitted"
    assert m.forced is False
    assert m.steps == 2
    assert len(news.calls) == 1
    assert len(m.assignments) == 1


# ----- the tool-call budget and the shared-budget gate -----


def test_tool_call_budget_forces_a_terminal(fake):
    personas = [_persona("ada", "tech")]
    for q in ("q1", "q2", "q3"):  # the model keeps searching, never assigns
        fake.state.script_chat(_search(q))

    news = FakeNews()
    m = _run(fake, news, personas, [], n_max=4, max_steps=10, tool_call_budget=2, circuit_breaker=5)

    assert m.stop_reason == "tool_budget"
    assert m.forced is True
    assert len(news.calls) == 2  # exactly the budget; the 3rd search never executed


def test_exhausted_budget_forces_a_terminal(fake):
    personas = [_persona("ada", "tech")]
    # The first turn's response reports usage that blows a tiny token budget.
    fake.state.script_chat(_search("expensive query"), usage={"total_tokens": 500})

    budget = ArticleBudget(token_budget=100, wall_clock_s=1e9, clock=lambda: 0.0)
    m = _run(fake, FakeNews(), personas, [], n_max=4, max_steps=6, budget=budget)

    assert m.stop_reason == "budget_exhausted"
    assert m.forced is True
    assert m.steps == 1  # it stopped at the top of the second turn


# ----- two-layer triage: the model's verdict is honored on top of the rules floor -----


def test_model_can_demote_a_rules_followup_to_new(fake):
    personas = [_persona("ada", "tech")]
    coverage = [CoverageItem(section="tech", headline="OpenAI releases GPT-6 to developers",
                             entities=["OpenAI", "GPT-6"], slug="openai-gpt6")]
    # Rules would call this a FOLLOW_UP (token overlap), but the model judges it NEW.
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "GPT-6 pricing draws complaints",
         "angle": "the pricing backlash", "entities": ["GPT-6"], "triage": "new"},
    ]))
    m = _run(fake, FakeNews(), personas, coverage, n_max=4, followup_threshold=0.2)

    assert len(m.assignments) == 1
    assert m.assignments[0].triage is Triage.NEW  # the model's call won
    assert m.assignments[0].follow_up_slug is None
    assert "FOLLOW-UP" not in m.assignments[0].angle


def test_model_can_flag_a_duplicate_the_rules_missed(fake):
    personas = [_persona("ada", "tech")]
    # Coverage is empty, so the rules say NEW; the model still drops it as a duplicate.
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "a story", "angle": "a story", "triage": "duplicate"},
    ]))
    m = _run(fake, FakeNews(), personas, [], n_max=4)

    assert m.assignments == []
    assert m.dropped_duplicates == 1


def test_rules_duplicate_is_a_hard_floor_the_model_cannot_override(fake):
    personas = [_persona("ada", "tech")]
    coverage = [CoverageItem(section="tech", headline="OpenAI releases GPT-6 to developers",
                             entities=["OpenAI", "GPT-6"], slug="openai-gpt6")]
    # The model insists it is NEW, but the rules say DUPLICATE: never republish.
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "OpenAI releases GPT-6 to developers",
         "angle": "...", "entities": ["OpenAI", "GPT-6"], "triage": "new"},
    ]))
    m = _run(fake, FakeNews(), personas, coverage, n_max=4)

    assert m.assignments == []
    assert m.dropped_duplicates == 1


def test_hallucinated_follow_up_slug_falls_back_to_the_matched_article(fake):
    personas = [_persona("ada", "tech")]
    coverage = [CoverageItem(section="tech", headline="OpenAI releases GPT-6 to developers",
                             entities=["OpenAI", "GPT-6"], slug="openai-gpt6")]
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "GPT-6 pricing draws complaints",
         "angle": "the backlash", "entities": ["GPT-6"],
         "triage": "follow_up", "follow_up_slug": "a-slug-that-does-not-exist"},
    ]))
    m = _run(fake, FakeNews(), personas, coverage, n_max=4, followup_threshold=0.2)

    assert len(m.assignments) == 1
    spec = m.assignments[0]
    assert spec.triage is Triage.FOLLOW_UP
    assert spec.follow_up_slug == "openai-gpt6"  # the real matched slug, not the hallucination
    assert "a-slug-that-does-not-exist" not in spec.angle


def test_empty_fingerprint_story_is_still_caught_as_a_duplicate(fake):
    # A degenerate headline (only an acronym) must not slip the duplicate floor.
    personas = [_persona("ada", "tech")]
    coverage = [CoverageItem(section="tech", headline="EU", entities=[], slug="eu")]
    fake.state.script_chat(_assign([{"persona_id": "ada", "headline": "EU", "angle": "EU"}]))
    m = _run(fake, FakeNews(), personas, coverage, n_max=4)

    assert m.assignments == []
    assert m.dropped_duplicates == 1


# ----- a broken backend degrades, it does not crash the run -----


def test_chat_transport_failure_degrades_without_raising(fake, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("backend 503")

    monkeypatch.setattr(manager_mod, "chat", _boom)
    personas = [_persona("ada", "tech")]

    m = _run(fake, FakeNews(), personas, [], n_max=4, max_steps=8, circuit_breaker=2)

    # The "run never fails here" contract holds: a Manifest is returned, not an exception.
    assert m.stop_reason == "circuit_breaker"
    assert m.assignments == []
    assert m.forced is True
