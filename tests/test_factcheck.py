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
    assert out == body and result.ok and result.citation.ok and result.preservation.ok
    assert fake.state.chat_requests == []  # nothing was sent


def test_failing_body_triggers_exactly_one_revise(fake):
    fake.state.script_chat("A corrected article grounded only in https://src.test/a.")
    body = "Bad article citing https://made-up.test/x and a TODO."
    out, result = fact_check(body, ledger=_ledger("https://src.test/a"), cfg=_cfg(fake),
                             prompts_dir=_prompts_dir())
    assert "made-up.test" not in out  # the revise replaced the fabricated citation
    assert result.ok and result.citation.ok
    assert len(fake.state.chat_requests) == 1  # exactly one bounded revise


def test_revise_is_bounded_to_one_pass_even_if_still_failing(fake):
    # The revise still leaves a fabricated URL; fact_check does NOT loop again.
    fake.state.script_chat("Still bad, cites https://made-up.test/y.")
    body = "Bad article citing https://made-up.test/x."
    out, result = fact_check(body, ledger=_ledger("https://src.test/a"), cfg=_cfg(fake),
                             prompts_dir=_prompts_dir())
    assert not result.ok  # one pass did not clear it...
    assert not result.citation.ok  # the residual fault is a citation fault
    assert len(fake.state.chat_requests) == 1  # ...and it did NOT revise again


# ----- preservation gate folded into the same single bounded revise -----


def _grounding_ledger() -> Ledger:
    """A ledger whose source text names Milei and the year 2023, so a body that
    respells the name or invents a different year is ungrounded against it."""
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="Javier Milei firmo el decreto",
            url="https://src.test/a", snippet="El presidente Javier Milei asumio en 2023")
    return led


def test_preservation_failure_routes_to_the_single_bounded_revise(fake):
    # Citations are clean (no URLs, no markers), but the body invents a year (2099) and
    # respells the entity (Miley). That must drive the ONE bounded revise, with both
    # problems named in the same prompt.
    fake.state.script_chat("El presidente Javier Milei firmo el decreto en 2023.")
    body = "El presidente Javier Miley firmo el decreto el 5 de enero de 2099."
    out, result = fact_check(body, ledger=_grounding_ledger(), entities=["Javier Milei"],
                             cfg=_cfg(fake), prompts_dir=_prompts_dir())

    assert len(fake.state.chat_requests) == 1  # exactly one bounded revise
    prompt = fake.state.chat_requests[0]["body"]["messages"][0]["content"]
    assert "2099" in prompt  # the invented year is named
    assert "Javier Miley" in prompt and "Javier Milei" in prompt  # the respelling and its fix
    # The revised body restored the name and the year, so the re-checked grounding is ok.
    assert out == "El presidente Javier Milei firmo el decreto en 2023."
    assert result.ok and result.preservation.ok and result.citation.ok


def test_clean_body_with_entities_makes_no_model_call(fake):
    # Preservation passing (name verbatim, year grounded) keeps the no-call fast path.
    body = "El presidente Javier Milei asumio en 2023."
    out, result = fact_check(body, ledger=_grounding_ledger(), entities=["Javier Milei"],
                             cfg=_cfg(fake), prompts_dir=_prompts_dir())
    assert out == body and result.ok
    assert fake.state.chat_requests == []
