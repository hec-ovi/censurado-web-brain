"""The shared per-article budget: token and wall-clock exhaustion.

A budget bounds the WORK an article may consume, never a single generation's
length. These tests pin both limits and the debit paths with an injected clock, so
nothing sleeps.
"""

from __future__ import annotations

from newsroom.inference import ChatResponse
from newsroom.pipeline import ArticleBudget


def test_token_exhaustion():
    b = ArticleBudget(token_budget=100, wall_clock_s=1e9, clock=lambda: 0.0)
    assert not b.exhausted()
    b.debit_tokens(60)
    assert not b.exhausted()
    b.debit_tokens(40)  # reaches the ceiling exactly
    assert b.exhausted()
    assert b.spent_tokens == 100


def test_wall_clock_exhaustion_with_injected_clock():
    now = [0.0]
    b = ArticleBudget(token_budget=1_000_000, wall_clock_s=10, clock=lambda: now[0])
    assert not b.exhausted()
    now[0] = 9.9
    assert not b.exhausted()
    now[0] = 10.0  # reaches the ceiling
    assert b.exhausted()


def test_debit_response_reads_total_tokens():
    b = ArticleBudget(token_budget=100, wall_clock_s=1e9, clock=lambda: 0.0)
    b.debit_response(ChatResponse(content="x", usage={"total_tokens": 70}))
    assert b.spent_tokens == 70
    # A response with no usage debits nothing (never negative).
    b.debit_response(ChatResponse(content="y"))
    assert b.spent_tokens == 70


def test_negative_or_missing_tokens_count_as_zero():
    b = ArticleBudget(token_budget=100, wall_clock_s=1e9, clock=lambda: 0.0)
    b.debit_tokens(-5)
    b.debit_tokens(0)
    assert b.spent_tokens == 0
    assert not b.exhausted()
