"""The collapsed evaluator: model-driven on a distinct endpoint, rules-degraded
when it shares the drafter's endpoint.

The model path drives the real ``chat`` adapter against the fake with a scripted
verdict; the rules path makes no model call and is pure logic over the ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone

from newsroom.config import load_settings
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.pipeline import evaluate_draft
from newsroom.research.ledger import Ledger


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="x", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"],
    )


def _ledger(*urls: str) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    for i, url in enumerate(urls):
        led.add(claim=f"c{i}", url=url, snippet="s")
    return led


def _prompts_dir():
    return load_settings().prompts_dir


def _drafter(fake):
    return _cfg(fake, "drafter-model")


# ----- model-driven (distinct endpoint) -----


def test_model_pass_verdict(fake):
    fake.state.script_chat('{"verdict": "PASS", "feedback": "", "failing_sections": []}')
    ev = evaluate_draft("the draft", outline="o", ledger=_ledger("https://s.test/a"),
                        drafter_cfg=_drafter(fake), evaluator_cfg=_cfg(fake, "evaluator-model"),
                        prompts_dir=_prompts_dir())
    assert ev.passed and ev.mode == "model"


def test_model_revise_with_failing_sections(fake):
    fake.state.script_chat('{"verdict": "REVISE", "feedback": "fix lede", "failing_sections": ["lede", "context"]}')
    ev = evaluate_draft("d", outline="o", ledger=_ledger(), drafter_cfg=_drafter(fake),
                        evaluator_cfg=_cfg(fake, "evaluator-model"), prompts_dir=_prompts_dir())
    assert ev.verdict == "REVISE"
    assert ev.failing_sections == ["lede", "context"]
    assert ev.feedback == "fix lede"


def test_model_tolerates_code_fence(fake):
    fake.state.script_chat('```json\n{"verdict": "PASS"}\n```')
    ev = evaluate_draft("d", outline="o", ledger=_ledger(), drafter_cfg=_drafter(fake),
                        evaluator_cfg=_cfg(fake, "evaluator-model"), prompts_dir=_prompts_dir())
    assert ev.passed


def test_unparseable_evaluation_is_treated_as_revise(fake):
    fake.state.script_chat("not json at all")
    ev = evaluate_draft("d", outline="o", ledger=_ledger(), drafter_cfg=_drafter(fake),
                        evaluator_cfg=_cfg(fake, "evaluator-model"), prompts_dir=_prompts_dir())
    assert ev.verdict == "REVISE"  # fail safe, never a crash


def test_model_verdict_wrapped_in_prose_is_parsed(fake):
    # A chatty local model that brackets its verdict with prose must still parse,
    # otherwise it would never PASS and would burn the whole sweep budget.
    fake.state.script_chat('Sure! My verdict:\n{"verdict": "PASS"}\nLet me know if you need more.')
    ev = evaluate_draft("d", outline="o", ledger=_ledger(), drafter_cfg=_drafter(fake),
                        evaluator_cfg=_cfg(fake, "evaluator-model"), prompts_dir=_prompts_dir())
    assert ev.passed


# ----- rules-degraded (shared endpoint) -----


def test_rules_pass_when_grounded_and_clean(fake):
    led = _ledger("https://s.test/a")
    ev = evaluate_draft("Grounded prose citing https://s.test/a.", outline="o", ledger=led,
                        drafter_cfg=_drafter(fake), evaluator_cfg=_drafter(fake),
                        prompts_dir=_prompts_dir())
    assert ev.passed and ev.mode == "rules"
    assert fake.state.chat_requests == []  # the degrade makes no model call


def test_rules_revise_on_empty_ledger(fake):
    ev = evaluate_draft("Ungrounded prose.", outline="o", ledger=_ledger(),
                        drafter_cfg=_drafter(fake), evaluator_cfg=_drafter(fake),
                        prompts_dir=_prompts_dir())
    assert ev.verdict == "REVISE" and "grounding" in ev.failing_sections


def test_rules_revise_on_fabricated_citation(fake):
    led = _ledger("https://s.test/a")
    ev = evaluate_draft("Cites https://made-up.test/x.", outline="o", ledger=led,
                        drafter_cfg=_drafter(fake), evaluator_cfg=_drafter(fake),
                        prompts_dir=_prompts_dir())
    assert ev.verdict == "REVISE" and "citations" in ev.failing_sections
