"""Fact-check: deterministic CitationVerify plus one bounded persona-blind revise.

CitationVerify is pure (no model). The revise path drives the real ``chat`` adapter
against the shared fake so the no-cap guard covers it too.
"""

from __future__ import annotations

from datetime import datetime, timezone

from newsroom.config import load_settings
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.pipeline import citation_verify, fact_check
from newsroom.research.ledger import Ledger


def _ledger(*urls: str) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    for i, url in enumerate(urls):
        led.add(claim=f"c{i}", url=url, snippet="s")
    return led


def _cfg(fake) -> ProviderConfig:
    return ProviderConfig(
        role="writer", provider="local", base_url=f"{fake.base_url}/v1",
        model="drafter-model", **DIALECTS["local"],
    )


def _prompts_dir():
    return load_settings().prompts_dir


# ----- CitationVerify (pure) -----


def test_clean_body_passes():
    assert citation_verify("Plain prose, no links.", _ledger()).ok


def test_cited_ledger_url_passes_even_with_trailing_punctuation():
    led = _ledger("https://src.test/a")
    res = citation_verify("As reported (see https://src.test/a).", led)
    assert res.ok and not res.unsupported_urls


def test_fabricated_url_is_unsupported():
    led = _ledger("https://src.test/a")
    res = citation_verify("Sourced from https://made-up.test/x here.", led)
    assert not res.ok
    assert res.unsupported_urls == ["https://made-up.test/x"]


def test_todo_markers_are_flagged():
    res = citation_verify("Intro. TODO: add numbers. [citation needed] and FIXME.", _ledger())
    assert not res.ok
    assert set(m.upper() for m in res.todo_markers) >= {"TODO", "FIXME"}


# ----- fact_check (deterministic + one bounded revise) -----


def test_clean_body_makes_no_model_call(fake):
    body = "A grounded article with no fabricated links."
    out, result = fact_check(body, ledger=_ledger("https://src.test/a"), cfg=_cfg(fake),
                             prompts_dir=_prompts_dir())
    assert out == body and result.ok
    assert fake.state.chat_requests == []  # nothing was sent


def test_failing_body_triggers_exactly_one_revise(fake):
    fake.state.script_chat("A corrected article grounded only in https://src.test/a.")
    body = "Bad article citing https://made-up.test/x and a TODO."
    out, result = fact_check(body, ledger=_ledger("https://src.test/a"), cfg=_cfg(fake),
                             prompts_dir=_prompts_dir())
    assert "made-up.test" not in out  # the revise replaced the fabricated citation
    assert result.ok
    assert len(fake.state.chat_requests) == 1  # exactly one bounded revise


def test_revise_is_bounded_to_one_pass_even_if_still_failing(fake):
    # The revise still leaves a fabricated URL; fact_check does NOT loop again.
    fake.state.script_chat("Still bad, cites https://made-up.test/y.")
    body = "Bad article citing https://made-up.test/x."
    out, result = fact_check(body, ledger=_ledger("https://src.test/a"), cfg=_cfg(fake),
                             prompts_dir=_prompts_dir())
    assert not result.ok  # one pass did not clear it...
    assert len(fake.state.chat_requests) == 1  # ...and it did NOT revise again
