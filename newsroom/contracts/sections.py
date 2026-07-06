"""The newsroom's own section vocabulary.

``section`` is a FREE STRING at the platform seam: the publish API slugifies
whatever it receives into ``/section/<slug>`` navigation with no server-side
enum, so a typo would silently mint an orphan section page on the public site.
The newsroom therefore pins its OWN closed vocabulary and validates locally before
publishing. Topics in scope per the project brief: AI/technology, world news,
politics, the markets/power beat published as "misterio y conspiración" (slug
``misterio-y-conspiracion``; it is a first-class section, not the old ``economics``
alias that was only relabelled in Spanish at render time), and culture/literature
(slug ``literatura``, rendered "Cultura y literatura"). This set mirrors the site's
section nav in the generator.
"""

from __future__ import annotations

__all__ = ["SECTION_ENUM", "is_valid_section"]

SECTION_ENUM: tuple[str, ...] = ("tech", "world", "politics", "misterio-y-conspiracion", "literatura")
"""The closed set of sections the newsroom will publish into."""


def is_valid_section(section: str) -> bool:
    """True if ``section`` is one of the newsroom's pinned sections (exact match)."""
    return section in SECTION_ENUM
