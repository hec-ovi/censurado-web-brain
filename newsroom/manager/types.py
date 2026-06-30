"""The coverage triage verdict: how a candidate story relates to what was published.

``Triage`` is the deterministic coverage decision (NEW / FOLLOW_UP / DUPLICATE) that
``newsroom.manager.coverage.classify`` returns from the fingerprint comparison.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["Triage"]


class Triage(str, Enum):
    """How a candidate story relates to what the harness has already published.

    * ``NEW``       a story we have not covered.
    * ``FOLLOW_UP`` a story we have covered that has new developments (extend + cite).
    * ``DUPLICATE`` the same event with no new information (drop, never republish).
    """

    NEW = "new"
    FOLLOW_UP = "follow_up"
    DUPLICATE = "duplicate"
