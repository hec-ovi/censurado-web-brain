"""Operator-editable editorial config: WHERE the news comes from and HOW it is written.

Three brain-owned, in-process stores over the editorial tables in ``newsroom.db``:

* ``location``  the single publication place (region, language, optional city) that
  scopes discovery and frames the manager ("you are the editor for <place>").
* ``portals``   the operator's own local news portals (e.g. ``example.com``,
  ``news.example``), the allowlist discovery pulls from and scopes search to.
* ``style``     the house style guide (the "model of redaction + rules"), kept as
  immutable versions plus an active pointer so an edit is a new version and
  promote/rollback is just moving the pointer.

Each store mirrors ``newsroom.personas.store``: a typed dataclass, a store over an
open ``sqlite3.Connection``, an injectable ``clock`` for deterministic timestamps, and
no network. ``config.Settings`` provides the boot seed; the live, editable copy lives
in these tables so a console edit takes effect without restarting the brain.
"""

from __future__ import annotations

from newsroom.editorial.lexicon import banned_terms_found
from newsroom.editorial.location import DEFAULT_LOCATION, Location, LocationStore
from newsroom.editorial.portals import Portal, PortalStore, normalize_domain, portal_slug
from newsroom.editorial.prompts_store import PromptStore, PromptTemplate
from newsroom.editorial.render import style_for_draft, style_for_eval
from newsroom.editorial.style import StyleGuide, StyleStore

__all__ = [
    "Location",
    "LocationStore",
    "DEFAULT_LOCATION",
    "Portal",
    "PortalStore",
    "portal_slug",
    "normalize_domain",
    "StyleGuide",
    "StyleStore",
    "PromptStore",
    "PromptTemplate",
    "style_for_draft",
    "style_for_eval",
    "banned_terms_found",
]
