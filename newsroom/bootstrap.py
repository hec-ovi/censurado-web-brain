"""One command that makes a fresh box a working newsroom: seed the editorial config.

``python -m newsroom bootstrap`` (or ``censurado-brain bootstrap``) opens the brain
database, idempotently seeds the editorial config (location, style, plus any default
portals/personas), and mirrors the platform author registry into the local personas.
Safe to run on every container start: seeding is find-or-create, so a re-run never
clobbers an operator's edits.
"""

from __future__ import annotations

from collections.abc import Callable
from sqlite3 import Connection

from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.editorial.location import Location
from newsroom.editorial.seeds import seed_all
from newsroom.mirror import (
    ReconcileResult,
    WebAuthor,
    fetch_web_authors,
    reconcile_personas,
)
from newsroom.personas import PersonaStore

__all__ = ["bootstrap", "AuthorFetcher"]

# A fetcher returns the platform's live author list. Injectable so the reconcile is
# tested with a fake registry instead of a real GET against the platform.
AuthorFetcher = Callable[[], list[WebAuthor]]


def _default_author_fetcher(settings: Settings) -> AuthorFetcher:
    """The production fetch: GET the platform author registry with the operator key.
    Yields [] when no operator token is configured, so the reconcile no-ops and a box
    without platform credentials still bootstraps on its local personas."""

    def fetch() -> list[WebAuthor]:
        if not settings.operator_token:
            return []
        return fetch_web_authors(settings.publish_base_url, settings.operator_token)

    return fetch


def _reconcile_from_web(
    settings: Settings, conn: Connection, fetch_authors: AuthorFetcher | None
) -> ReconcileResult:
    """Mirror the platform author registry into the local personas, BEST-EFFORT: if the
    platform is unreachable (the fetch raises) the reconcile is a skipped no-op and the
    boot proceeds on the local personas. Web being down must never empty the newsroom."""
    fetch = fetch_authors or _default_author_fetcher(settings)
    try:
        authors = fetch()
    except Exception:
        return ReconcileResult(skipped=True)
    return reconcile_personas(PersonaStore(conn), authors)


def bootstrap(
    settings: Settings,
    *,
    fetch_authors: AuthorFetcher | None = None,
    **seed_overrides,
) -> dict:
    """Seed the newsroom idempotently and mirror the platform authors. Returns a summary
    with the per-category seed result and the reconcile result.

    The reconcile runs AFTER the seed, so a soft-deactivated author is reflected.
    ``fetch_authors`` defaults to production; tests inject a double to assert the wiring
    without a real platform. ``seed_overrides`` are forwarded to ``seed_all``
    (location/portals/personas/style)."""
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    # Seed the publication place from the configured presentation defaults, unless a
    # caller overrode it.
    seed_overrides.setdefault(
        "location",
        Location(
            region=settings.default_region,
            ui_lang=settings.default_ui_lang,
            language=settings.default_language,
            gdelt_country=settings.default_gdelt_country,
        ),
    )
    seeded = seed_all(conn, **seed_overrides)
    reconciled = _reconcile_from_web(settings, conn, fetch_authors)
    return {
        "seeded": seeded.as_dict(),
        "reconciled": reconciled.as_dict(),
    }
