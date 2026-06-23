"""The harness's own section vocabulary.

``section`` is a FREE STRING at the platform seam: the publish API slugifies
whatever it receives into ``/section/<slug>`` navigation with no server-side
enum, so a typo would silently mint an orphan section page on the public site.
The harness therefore pins its OWN closed vocabulary and validates locally before
publishing. Topics in scope per the project brief: AI/technology, world news,
politics, economics.
"""

from __future__ import annotations

__all__ = ["SECTION_ENUM", "is_valid_section"]

SECTION_ENUM: tuple[str, ...] = ("tech", "world", "politics", "economics")
"""The closed set of sections the harness will publish into."""


def is_valid_section(section: str) -> bool:
    """True if ``section`` is one of the harness's pinned sections (exact match)."""
    return section in SECTION_ENUM
