"""The bounded research loop: plan-then-search, grounding-first.

Research is NOT a model-driven ReAct loop. The model plans once (3 to 6
sub-questions for the assignment), then code searches each deterministically and
records sources in the ledger. The architecture's three-guard rule applies:

  * ``max_steps`` (``max_research_steps``, default 6) caps the number of searches.
  * a LEDGER-STALL detector: a step that adds zero new-URL rows is no progress;
    after ``stall_limit`` (``research_stall_limit``, default 2) consecutive stalls
    the loop aborts. This is the guard a circuit breaker misses: K DISTINCT,
    successful searches that each return junk would defeat an identical-call
    breaker, but each adds no new ledger row, so the stall detector catches them.
  * an optional shared per-article ``budget`` (Step 5 owns it): checked before each
    search, so research cannot overrun the article's token/wall-clock ceiling.

The loop is deterministic and reads top to bottom; the only model call is the
plan, and it is uncapped like every generation in the harness.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from newsroom.inference import ChatRequest, chat
from newsroom.inference.provider import ProviderConfig
from newsroom.jsonio import extract_json
from newsroom.prompts import load_prompt, render
from newsroom.research.ledger import Ledger

__all__ = ["ResearchOutcome", "plan_subquestions", "run_research"]

# A search is any callable: a query string -> a list of hits (objects with
# ``url`` / ``snippet`` attributes, or dicts). ``ResearchTool.search`` fits.
SearchFn = Callable[[str], list]


@dataclass
class ResearchOutcome:
    steps: int
    stop_reason: str  # plan_exhausted | max_steps | stalled | budget_exhausted
    subquestions: list[str]
    ledger: Ledger = field(repr=False)


def plan_subquestions(topic: str, *, cfg: ProviderConfig, prompts_dir: Path | str) -> list[str]:
    """One model call: turn the assignment into 3 to 6 searchable sub-questions.
    Accepts a JSON array or an object with a ``sub_questions`` key; returns the
    non-empty questions as strings."""
    template = load_prompt(prompts_dir, "journalist", "research.md")
    prompt = render(template, topic=topic)
    response = chat(ChatRequest(messages=[{"role": "user", "content": prompt}]), cfg=cfg)
    data = extract_json(response.content)
    if isinstance(data, dict):
        data = data.get("sub_questions") or data.get("subquestions") or []
    if not isinstance(data, list):
        return []
    return [str(q).strip() for q in data if str(q).strip()]


def run_research(
    topic: str,
    search: SearchFn,
    *,
    cfg: ProviderConfig | None = None,
    prompts_dir: Path | str | None = None,
    ledger: Ledger | None = None,
    subquestions: list[str] | None = None,
    max_steps: int = 6,
    stall_limit: int = 2,
    budget=None,
    clock: Callable[[], datetime] | None = None,
) -> ResearchOutcome:
    """Plan (unless ``subquestions`` is given), then search each sub-question into
    the ledger under the three guards. ``budget`` (optional) needs an
    ``exhausted() -> bool`` method. Returns the steps taken, the stop reason, the
    plan, and the filled ledger."""
    led = ledger if ledger is not None else Ledger(clock=clock)
    if subquestions is None:
        if cfg is None or prompts_dir is None:
            raise ValueError("planning requires cfg and prompts_dir (or pass subquestions)")
        subquestions = plan_subquestions(topic, cfg=cfg, prompts_dir=prompts_dir)

    stall = 0
    steps = 0
    reason = "plan_exhausted"
    for sub_q in subquestions:
        if steps >= max_steps:
            reason = "max_steps"
            break
        if budget is not None and budget.exhausted():
            reason = "budget_exhausted"
            break
        results = search(sub_q)
        steps += 1
        if led.add_results(results, claim=sub_q) == 0:
            stall += 1
            if stall >= stall_limit:
                reason = "stalled"
                break
        else:
            stall = 0

    return ResearchOutcome(
        steps=steps, stop_reason=reason, subquestions=list(subquestions), ledger=led
    )
