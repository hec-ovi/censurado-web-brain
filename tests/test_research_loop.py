"""The bounded research loop: plan-then-search under max_steps + ledger-stall.

Planning hits the real ``chat`` adapter against the shared fake (scripted
sub-questions). Searching is driven by a recording test double so a test can make
searches DISTINCT-but-useless (each returns results, none introduce a new URL):
the stall detector must catch that even though a circuit breaker would not. The
cap test makes every search productive and asserts the loop stops at max_steps.
"""

from __future__ import annotations

import json

import pytest

from newsroom.config import load_settings
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.research import SearchResult, plan_subquestions, run_research


def _cfg(fake) -> ProviderConfig:
    return ProviderConfig(
        role="researcher",
        provider="local",
        base_url=f"{fake.base_url}/v1",
        model="local-gemma",
        **DIALECTS["local"],
    )


def _prompts_dir():
    return load_settings().prompts_dir


class RecordingSearch:
    """Wraps a query->results function and records the queries it received."""

    def __init__(self, fn):
        self._fn = fn
        self.queries: list[str] = []

    def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        return self._fn(query)


class _Budget:
    def __init__(self, exhausted: bool):
        self._exhausted = exhausted

    def exhausted(self) -> bool:
        return self._exhausted


def _unique(query: str) -> list[SearchResult]:
    # A fresh, productive source for every distinct query.
    return [SearchResult(url=f"https://src.test/{query}", snippet=f"about {query}")]


# ----- planning (real chat against the fake) -----


def test_plan_subquestions_parses_json_array(fake):
    fake.state.script_chat(json.dumps(["who won", "by how much", "what is disputed"]))
    qs = plan_subquestions("the 2026 runoff", cfg=_cfg(fake), prompts_dir=_prompts_dir())
    assert qs == ["who won", "by how much", "what is disputed"]


def test_plan_subquestions_accepts_object_form(fake):
    fake.state.script_chat(json.dumps({"sub_questions": ["a", "b"]}))
    assert plan_subquestions("t", cfg=_cfg(fake), prompts_dir=_prompts_dir()) == ["a", "b"]


def test_plan_subquestions_tolerates_code_fence(fake):
    fake.state.script_chat("```json\n" + json.dumps(["q1", "q2"]) + "\n```")
    assert plan_subquestions("t", cfg=_cfg(fake), prompts_dir=_prompts_dir()) == ["q1", "q2"]


def test_planning_request_carries_no_length_cap(fake):
    fake.state.script_chat(json.dumps(["q1"]))
    plan_subquestions("t", cfg=_cfg(fake), prompts_dir=_prompts_dir())
    body = fake.state.chat_requests[-1]["body"]
    assert "max_tokens" not in body and "max_completion_tokens" not in body


# ----- the loop's guards -----


def test_productive_plan_fills_ledger_and_exhausts_naturally():
    search = RecordingSearch(_unique)
    out = run_research(
        "t", search.search, subquestions=["q1", "q2", "q3"], max_steps=6, stall_limit=2
    )
    assert out.steps == 3
    assert out.stop_reason == "plan_exhausted"
    assert len(out.ledger) == 3
    assert search.queries == ["q1", "q2", "q3"]


def test_stall_detector_aborts_on_distinct_but_useless_searches():
    # Every distinct query returns the SAME already-seen URL, so after the first
    # productive step each search adds zero new rows: a stall a circuit breaker
    # (which only catches identical/failed calls) would miss.
    def same_url(query: str) -> list[SearchResult]:
        return [SearchResult(url="https://src.test/only", snippet=f"about {query}")]

    search = RecordingSearch(same_url)
    out = run_research(
        "t",
        search.search,
        subquestions=["q1", "q2", "q3", "q4", "q5"],
        max_steps=6,
        stall_limit=2,
    )
    assert out.stop_reason == "stalled"
    assert out.steps == 3  # q1 productive, q2 + q3 are the two consecutive stalls
    assert len(out.ledger) == 1
    assert search.queries == ["q1", "q2", "q3"]  # the searches WERE distinct


def test_max_steps_caps_a_never_stalling_loop():
    search = RecordingSearch(_unique)  # every search is productive, never stalls
    out = run_research(
        "t",
        search.search,
        subquestions=[f"q{i}" for i in range(10)],
        max_steps=6,
        stall_limit=2,
    )
    assert out.stop_reason == "max_steps"
    assert out.steps == 6
    assert len(out.ledger) == 6
    assert len(search.queries) == 6  # it stopped searching at the cap


def test_exhausted_budget_stops_before_any_search():
    search = RecordingSearch(_unique)
    out = run_research(
        "t",
        search.search,
        subquestions=["q1", "q2"],
        max_steps=6,
        stall_limit=2,
        budget=_Budget(exhausted=True),
    )
    assert out.stop_reason == "budget_exhausted"
    assert out.steps == 0
    assert search.queries == []


def test_run_research_plans_then_searches_end_to_end(fake):
    fake.state.script_chat(json.dumps(["who", "what", "when"]))
    search = RecordingSearch(_unique)
    out = run_research(
        "the assignment", search.search, cfg=_cfg(fake), prompts_dir=_prompts_dir(), max_steps=6
    )
    assert out.subquestions == ["who", "what", "when"]
    assert out.steps == 3
    assert len(out.ledger) == 3


def test_planning_requires_cfg_when_no_subquestions():
    with pytest.raises(ValueError, match="planning requires"):
        run_research("t", RecordingSearch(_unique).search)
