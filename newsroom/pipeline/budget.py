"""The shared per-article budget: a token + wall-clock ceiling over the whole pipeline.

This is the article budget the architecture doc (A.8) requires every sub-loop to
debit from the SAME ceiling: research, the draft/evaluate sweeps, enrich,
fact-check, and finalize all charge their token usage here, so the budget is a
real bound over their sum, not just over one loop. The research loop (Step 4)
already accepts any object with ``exhausted() -> bool``; this is that object.

Crucially, a budget is NOT an output cap. It bounds how much WORK an article may
consume, never how long a single generation may be. When the budget is spent the
pipeline DROPS the assignment (``budget_exhausted``); it never truncates a body.
That keeps the loop-bound and the no-output-cap rule cleanly separate: a truncated
body would look like an output cap even though it was a loop bound, so we drop the
whole article instead (A.8).

Wall-clock uses an injectable monotonic clock (seconds) so a test can drive
elapsed time deterministically without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from newsroom.inference import ChatResponse

__all__ = ["ArticleBudget"]


class ArticleBudget:
    """A token + wall-clock ceiling for one article. ``exhausted()`` is True once
    either limit is reached; the pipeline checks it before and after each stage."""

    def __init__(
        self,
        *,
        token_budget: int,
        wall_clock_s: float,
        clock: Callable[[], float] | None = None,
    ):
        self._token_budget = token_budget
        self._wall_clock_s = wall_clock_s
        self._clock = clock or time.monotonic
        self._start = self._clock()
        self._spent_tokens = 0

    def debit_tokens(self, tokens: int) -> None:
        """Charge ``tokens`` against the budget (negative or missing counts as 0)."""
        self._spent_tokens += max(0, int(tokens or 0))

    def debit_response(self, response: ChatResponse) -> None:
        """Charge a chat response's reported ``total_tokens`` (0 if absent)."""
        self.debit_tokens((response.usage or {}).get("total_tokens", 0))

    @property
    def spent_tokens(self) -> int:
        return self._spent_tokens

    @property
    def elapsed_s(self) -> float:
        return self._clock() - self._start

    def exhausted(self) -> bool:
        return (
            self._spent_tokens >= self._token_budget
            or self.elapsed_s >= self._wall_clock_s
        )
