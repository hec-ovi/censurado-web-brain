"""Coverage memory: the dedup store that keeps published news fresh and non-repeating.

  * coverage memory   ``CoverageStore`` / ``classify`` for fresh, non-repeating,
                      continuous news.
"""

from __future__ import annotations

from newsroom.manager.coverage import (
    CoverageItem,
    CoverageStore,
    CoverageVerdict,
    classify,
    fingerprint,
    similarity,
)

__all__ = [
    "CoverageStore",
    "CoverageItem",
    "CoverageVerdict",
    "classify",
    "fingerprint",
    "similarity",
]
