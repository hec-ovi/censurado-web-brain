"""The batch planner fans the existing manager across authors, once per desk.

Each persona's manager is the SAME ``run_manager`` (scripted via the shared fake); a
discover-factory double records the per-author scoping (including the batch
``freshness``) and feeds each manager canned candidates. These pin the batch contracts
the build plan locks (P4):

  (a) ``discover`` is called ONCE PER AUTHOR, in order, with the batch timeframe;
  (b) the per-author manifests CONCATENATE with NO cross-author dedup: both desks'
      picks survive even when they cover the same event;
  (c) the round-robin interleave + ``max_total`` clamp keeps one article from each desk
      before any desk's second;
  (d) a desk that finds nothing forces a terminal but never sinks the batch.

The discovery scoping itself (author sources, place, freshness reaching the backend)
is pinned separately in ``test_research_scoping.py``; here ``discover`` is a double, so
these tests own the FAN, not the per-author search.
"""

from __future__ import annotations

import json

from newsroom.config import load_settings
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager import plan_batch
from newsroom.manager.types import Candidate
from newsroom.personas import Persona


def _cfg(fake) -> ProviderConfig:
    return ProviderConfig(
        role="manager", provider="local", base_url=f"{fake.base_url}/v1",
        model="manager-model", **DIALECTS["local"],
    )


def _prompts_dir():
    return load_settings().prompts_dir


def _persona(pid: str, beat: str) -> Persona:
    return Persona(id=pid, display_name=pid.title(), beat=beat, who_i_am=f"I cover {beat}.", style="dry")


def _assign(assignments) -> str:
    return json.dumps({"action": "assign", "assignments": assignments})


class _Discover:
    """A discover-factory double: ``(persona, *, freshness) -> search``. Records each call
    and returns a search yielding one canned candidate per author, except for personas
    named in ``empty`` (their desk finds nothing)."""

    def __init__(self, empty=()):
        self.calls: list[dict] = []
        self._empty = set(empty)

    def __call__(self, persona, *, freshness=None):
        self.calls.append({"persona_id": persona.id, "freshness": freshness})
        empty = persona.id in self._empty

        def search(query: str) -> list[Candidate]:
            if empty:
                return []
            return [Candidate(headline=f"{persona.id} story", section=persona.beat,
                              url=f"https://news.test/{persona.id}")]

        return search


# ----- (a) once per author, with the batch freshness -----


def test_plan_batch_discovers_once_per_author_with_freshness(fake):
    personas = [_persona("ada", "tech"), _persona("bea", "politics")]
    # Each desk's manager emits an assignment on its first turn (consumed in persona order).
    fake.state.script_chat(_assign([{"persona_id": "ada", "headline": "chip", "angle": "chip"}]))
    fake.state.script_chat(_assign([{"persona_id": "bea", "headline": "bill", "angle": "bill"}]))
    discover = _Discover()

    m = plan_batch(personas=personas, coverage=[], discover=discover, cfg=_cfg(fake),
                   prompts_dir=_prompts_dir(), per_author=2, timeframe_freshness="day", max_steps=2)

    assert [c["persona_id"] for c in discover.calls] == ["ada", "bea"]  # once per author, in order
    assert all(c["freshness"] == "day" for c in discover.calls)  # the batch timeframe reaches discovery
    assert {a.persona_id for a in m.assignments} == {"ada", "bea"}  # both desks contributed


# ----- (b) concatenate, NO cross-author dedup -----


def test_plan_batch_keeps_both_desks_on_the_same_story(fake):
    personas = [_persona("ada", "tech"), _persona("bea", "politics")]
    # Both desks land on the SAME event; the batch keeps BOTH (distinct desks on one
    # event is the point of a multi-desk newsroom, not a duplicate to collapse).
    fake.state.script_chat(_assign([{"persona_id": "ada", "headline": "el apagon", "angle": "el apagon"}]))
    fake.state.script_chat(_assign([{"persona_id": "bea", "headline": "el apagon", "angle": "el apagon"}]))

    m = plan_batch(personas=personas, coverage=[], discover=_Discover(), cfg=_cfg(fake),
                   prompts_dir=_prompts_dir(), per_author=2, max_steps=2)

    assert len(m.assignments) == 2
    assert sorted(a.persona_id for a in m.assignments) == ["ada", "bea"]


# ----- (c) round-robin interleave, then the overall clamp -----


def test_plan_batch_interleaves_then_clamps_to_max_total(fake):
    personas = [_persona("ada", "tech"), _persona("bea", "politics")]
    fake.state.script_chat(_assign([
        {"persona_id": "ada", "headline": "ada one", "angle": "ada one"},
        {"persona_id": "ada", "headline": "ada two", "angle": "ada two"},
    ]))
    fake.state.script_chat(_assign([
        {"persona_id": "bea", "headline": "bea one", "angle": "bea one"},
        {"persona_id": "bea", "headline": "bea two", "angle": "bea two"},
    ]))

    m = plan_batch(personas=personas, coverage=[], discover=_Discover(), cfg=_cfg(fake),
                   prompts_dir=_prompts_dir(), per_author=2, max_total=3, max_steps=2)

    assert len(m.assignments) == 3  # clamped from four (two desks x two each)
    # Round robin: one pick from EACH desk before either desk's second.
    assert {m.assignments[0].persona_id, m.assignments[1].persona_id} == {"ada", "bea"}
    assert m.assignments[2].persona_id == "ada"  # ada's second is the third pick; bea's second is dropped


# ----- (d) a stuck desk forces a terminal but never sinks the batch -----


def test_plan_batch_survives_a_desk_that_finds_nothing(fake):
    personas = [_persona("ada", "tech"), _persona("bea", "politics")]
    fake.state.script_chat(_assign([{"persona_id": "ada", "headline": "chip", "angle": "chip"}]))
    fake.state.script_chat(json.dumps({"action": "search", "query": "nada"}))  # bea's one turn finds nothing
    discover = _Discover(empty={"bea"})

    m = plan_batch(personas=personas, coverage=[], discover=discover, cfg=_cfg(fake),
                   prompts_dir=_prompts_dir(), per_author=2, max_steps=1)

    assert [a.persona_id for a in m.assignments] == ["ada"]  # ada ships; bea's empty desk doesn't sink it
    assert m.forced is True  # bea hit the step cap with nothing to assign -> forced terminal
