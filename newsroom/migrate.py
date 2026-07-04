"""The one-time personas.db -> platform backend data move.

This is the cut-over that ends the brain's ownership of content data. It lifts every
persona into the platform's author registry (POST /authors), every portal into the
source registry (POST /sources), and each persona's source links into the author_sources
join, so the platform becomes the single source of truth and personas.db can be deleted.

Two properties make it safe to run against live data:

  * IDEMPOTENT. Every write is an upsert keyed on handle/slug, so a re-run repairs a
    partial move without creating duplicates.
  * VERIFIED against what was ACTUALLY SENT. After the writes it reads the registry back
    and asserts each row equals the payload the move pushed (every scalar, full metadata
    equality, the source set, the tombstone flag), NOT a re-derivation of the same field
    map, so a bug shared by the push and the check cannot hide. It HARD-FAILS on any
    mismatch: unlike the old best-effort mirror push, the move must never silently lose the
    private prompt, the voice, or the few-shots that only ever lived in personas.db. It
    also cross-checks that every attached source slug resolves to a real source row
    (author_sources has no foreign key, so a dangling link would otherwise pass unseen).

INTENTIONAL DROPS (documented, not "every field"): a persona/portal's created_at and
updated_at are NOT carried; the platform restamps them at upsert (chronology is not
load-bearing, and the strict decoder would reject the extra keys). Everything else is
carried and verified.

The ORDER is fixed: sources first (so the author_sources join resolves against existing
rows), then authors (each carrying its source set in the same request), then tombstones for
retired identities. The field map and the tombstone policy are pure functions so they are
tested in isolation; the HTTP seams are injected so the orchestration is tested without a
network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from newsroom.editorial.portals import Portal
from newsroom.mirror.client import (
    DEFAULT_TIMEOUT,
    PushResult,
    delete_web_author,
    push_web_author,
    push_web_source,
)
from newsroom.personas.store import Persona

__all__ = [
    "author_fields",
    "source_fields",
    "has_content",
    "should_tombstone",
    "norm_slugs",
    "MoveReport",
    "run_move",
    "verify_move",
    "OWNED_METADATA_KEYS",
]

# The author-metadata keys the brain owns and (re)writes on the move. A backend author that
# already carries a metadata key OUTSIDE this set (e.g. one an operator added via the panel)
# would be dropped by the wholesale-replace upsert, so the move WARNS about it up front.
OWNED_METADATA_KEYS = frozenset(
    {"beat", "gender", "who_i_am", "language", "few_shots_pos", "few_shots_neg", "profile_topics"}
)


# ----- pure field maps (the column map, tested in isolation) -----


def author_fields(persona: Persona) -> dict:
    """A persona as the kwargs for ``push_web_author`` (POST /authors). The column map:

      id            -> handle (the join key + article.author)
      display_name  -> name
      about         -> bio AND about (layer 1 renders bio; about is the promoted column)
      style         -> style (first-class; the private voice, panel-editable, never public)
      gender        -> gender (first-class)
      avatar_path   -> avatar
      profile_topics-> topics (first-class) AND metadata.profile_topics (layer-1 overlay copy)
      sources       -> the author_sources join, set in the same request
      beat/who_i_am/language/few_shots_pos/few_shots_neg -> the metadata tail (the drafting
        agent + the Nosotros card read them there; they are not public columns).

    created_at/updated_at are intentionally NOT carried (the platform restamps them)."""
    metadata: dict[str, object] = {
        "beat": persona.beat,
        "who_i_am": persona.who_i_am,
        "language": persona.language,
        "few_shots_pos": list(persona.few_shots_pos),
        "few_shots_neg": list(persona.few_shots_neg),
    }
    # profile_topics rides in metadata too: layer 1 still overlays an author's topic list
    # from Metadata['profile_topics'], so keeping the copy keeps the move off Layer 1 until
    # it reads the first-class column. Sent only when curated (empty = fall back to the
    # computed union), matching the pre-move convention.
    if persona.profile_topics:
        metadata["profile_topics"] = list(persona.profile_topics)
    return {
        "handle": persona.id,
        "name": persona.display_name,
        "bio": persona.about,
        "about": persona.about,
        "avatar": persona.avatar_path,
        "gender": persona.gender,
        "style": persona.style,
        "topics": list(persona.profile_topics),
        "sources": norm_slugs(persona.sources),
        "metadata": metadata,
    }


# The portal fields the move carries + verifies (1:1 with the source registry, minus the
# platform-restamped created_at/updated_at).
_SOURCE_FIELDS = (
    "domain", "homepage", "description", "feed_urls", "feed_type", "language",
    "ownership_group", "lean", "enabled", "status", "last_checked", "last_ok",
)


def source_fields(portal: Portal) -> dict:
    """A portal as the kwargs for ``push_web_source`` (POST /sources). 1:1, with the slug
    sent explicitly (to preserve the exact id the author join resolves against) and enabled
    sent explicitly (the platform flag defaults to true, so a disabled portal must say so).
    created_at/updated_at are intentionally NOT carried (the platform restamps them)."""
    return {
        "domain": portal.domain,
        "slug": portal.id,
        "homepage": portal.homepage,
        "description": portal.description,
        "feed_urls": list(portal.feed_urls),
        "feed_type": portal.feed_type,
        "language": portal.language,
        "ownership_group": portal.ownership_group,
        "lean": portal.lean,
        "enabled": portal.enabled,
        "status": portal.status,
        "last_checked": portal.last_checked,
        "last_ok": portal.last_ok,
    }


def norm_slugs(slugs) -> list[str]:
    """Trim, drop blanks, and de-duplicate (order-preserving) a source-slug list the way the
    backend's SetAuthorSources normalizes it, so the verify compares like with like instead
    of false-failing on a stray blank or a whitespace-padded slug."""
    out: list[str] = []
    seen: set[str] = set()
    for s in slugs or []:
        t = str(s).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def has_content(persona: Persona) -> bool:
    """Whether an inactive persona carries any real content worth preserving (its prompt,
    bio, voice, few-shots, sources, or curated topics). Used to distinguish a genuinely
    empty shell (safe to skip) from a retired identity whose data must still be carried
    across, so no inactive persona's private data is dropped silently."""
    return bool(
        (persona.who_i_am or "").strip()
        or (persona.about or "").strip()
        or (persona.style or "").strip()
        or persona.few_shots_pos
        or persona.few_shots_neg
        or persona.sources
        or persona.profile_topics
    )


def should_tombstone(persona: Persona) -> bool:
    """Whether an inactive persona is carried across as a TOMBSTONED author.

    An inactive persona WITH content is carried (upserted so its fields are complete) and
    then tombstoned, so a retired identity keeps its record without being publicly listed.
    An inactive EMPTY shell is skipped entirely (see ``has_content``): it usually mirrors an
    author already live on the platform, so writing/tombstoning it would risk hiding that
    author. A live persona is never tombstoned."""
    return (not persona.active) and has_content(persona)


# ----- the move (injectable HTTP seams so it is tested without a network) -----

Push = Callable[[dict], PushResult]
Tombstone = Callable[[str], PushResult]
ReadRows = Callable[[], list]
CountArticles = Callable[[str], int]


@dataclass
class MoveReport:
    """What the move did, per row, plus the verify result. ``ok`` is True only when every
    write succeeded AND the read-back matched what was pushed with no discrepancy, so a
    caller keys the exit code (and the go/no-go on deleting personas.db) on it. ``warnings``
    are non-fatal notices the operator should read (e.g. backend metadata the move would
    overwrite, or a retired persona left live because it still has articles)."""

    sources_pushed: list[str] = field(default_factory=list)
    authors_pushed: list[str] = field(default_factory=list)
    tombstoned: list[str] = field(default_factory=list)
    skipped_inactive: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    discrepancies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False
    ok: bool = False

    def as_dict(self) -> dict:
        return {
            "sources_pushed": self.sources_pushed,
            "authors_pushed": self.authors_pushed,
            "tombstoned": self.tombstoned,
            "skipped_inactive": self.skipped_inactive,
            "failures": self.failures,
            "discrepancies": self.discrepancies,
            "warnings": self.warnings,
            "dry_run": self.dry_run,
            "ok": self.ok,
        }


def run_move(
    personas: list[Persona],
    portals: list[Portal],
    *,
    base_url: str,
    token: str,
    push_source: Push | None = None,
    push_author: Push | None = None,
    tombstone: Tombstone | None = None,
    read_authors: ReadRows | None = None,
    read_sources: ReadRows | None = None,
    count_articles: CountArticles | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> MoveReport:
    """Move every portal + persona to the platform, then verify the round-trip.

    Order: sources, then authors (each carrying its source join), then tombstone the retired
    identities. Every failure is recorded and the loop continues, so one bad row never aborts
    the rest; the verify pass then reads the registry back and the report's ``ok`` is the
    single go/no-go signal. The HTTP seams default to the real client bound to ``base_url``/
    ``token``; a test injects fakes."""
    base = base_url.rstrip("/")
    push_source = push_source or (lambda kw: push_web_source(base, token, timeout=timeout, **kw))
    push_author = push_author or (lambda kw: push_web_author(base, token, timeout=timeout, **kw))
    tombstone = tombstone or (lambda handle: delete_web_author(base, token, handle=handle, timeout=timeout))
    read_authors = read_authors or (lambda: _get_rows(base, token, "/authors?include_deleted=true", "authors", timeout))
    read_sources = read_sources or (lambda: _get_rows(base, token, "/sources?include_deleted=true", "sources", timeout))
    count_articles = count_articles or (lambda handle: _count_articles(base, token, handle, timeout))

    report = MoveReport()
    # What the move INTENDED to write, captured so verify checks the read-back against the
    # actual payload (never a re-derivation that could share a bug).
    expected_sources: dict[str, dict] = {}
    expected_authors: dict[str, dict] = {}   # handle -> {"body": <fields>, "deleted": bool}

    # Preflight: warn about backend author metadata the wholesale-replace upsert would drop.
    for row in read_authors():
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") or {}
        foreign = sorted(k for k in meta if k not in OWNED_METADATA_KEYS)
        if foreign:
            report.warnings.append(
                f"author {row.get('handle')!r} carries backend-only metadata {foreign} that "
                f"the move will OVERWRITE (personas.db is authoritative for owned fields)"
            )

    # 1) sources first, so an author's source-join resolves against existing rows.
    for portal in portals:
        fields = source_fields(portal)
        res = push_source(fields)
        if res.ok:
            report.sources_pushed.append(portal.id)
            expected_sources[portal.id] = fields
        else:
            report.failures.append({"kind": "source", "key": portal.id, "code": res.code or str(res.status), "detail": res.detail})

    # 2) authors: active ones live, retired identities carried across then (usually) tombstoned.
    for persona in personas:
        if persona.active:
            fields = author_fields(persona)
            res = push_author(fields)
            if res.ok:
                report.authors_pushed.append(persona.id)
                expected_authors[persona.id] = {"body": fields, "deleted": False}
            else:
                report.failures.append({"kind": "author", "key": persona.id, "code": res.code or str(res.status), "detail": res.detail})
        elif should_tombstone(persona):
            fields = author_fields(persona)
            res = push_author(fields)
            if not res.ok:
                report.failures.append({"kind": "author", "key": persona.id, "code": res.code or str(res.status), "detail": res.detail})
                continue
            # Never hide a live author: if the handle already has published articles, leave it
            # LIVE (its fields are now complete) and warn, instead of tombstoning it away.
            if count_articles(persona.id) > 0:
                report.warnings.append(
                    f"persona {persona.id!r} is inactive locally but its backend author has "
                    f"published articles; left LIVE (not tombstoned) so its content stays visible"
                )
                report.authors_pushed.append(persona.id)
                expected_authors[persona.id] = {"body": fields, "deleted": False}
                continue
            tomb = tombstone(persona.id)
            if tomb.ok:
                report.tombstoned.append(persona.id)
                expected_authors[persona.id] = {"body": fields, "deleted": True}
            else:
                report.failures.append({"kind": "tombstone", "key": persona.id, "code": tomb.code or str(tomb.status), "detail": tomb.detail})
        else:
            report.skipped_inactive.append(persona.id)

    # 3) verify the round-trip from the platform's own read side, against what was pushed.
    authors_by_handle = {a.get("handle"): a for a in read_authors() if isinstance(a, dict)}
    sources_by_slug = {s.get("slug"): s for s in read_sources() if isinstance(s, dict)}
    report.discrepancies = verify_move(expected_sources, expected_authors, authors_by_handle, sources_by_slug)

    report.ok = not report.failures and not report.discrepancies
    return report


def _get_rows(base: str, token: str, path: str, key: str, timeout: float) -> list:
    """GET a registry list endpoint and return its rows (``authors`` / ``sources``). Raises
    ``httpx.HTTPError`` on a fault so the move fails loud rather than verifying against an
    empty read."""
    resp = httpx.get(f"{base}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    rows = body.get(key, []) if isinstance(body, dict) else []
    return rows if isinstance(rows, list) else []


def _count_articles(base: str, token: str, handle: str, timeout: float) -> int:
    """How many articles the backend attributes to ``handle`` (GET /articles?author=). Used
    to avoid tombstoning a retired persona whose handle still has live content. Never raises
    (a read fault is treated as 'unknown -> 0', and the tombstone proceeds as before)."""
    try:
        resp = httpx.get(
            f"{base}/articles",
            params={"author": handle, "limit": "1"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        return 0
    return int(body.get("total", 0)) if isinstance(body, dict) else 0


def verify_move(
    expected_sources: dict,
    expected_authors: dict,
    authors_by_handle: dict,
    sources_by_slug: dict,
) -> list[str]:
    """Assert every pushed source + author round-tripped through the platform read side with
    no loss, comparing the read-back against what the move ACTUALLY pushed (not a re-derived
    field map). Returns a list of human-readable discrepancy strings (empty = clean). This is
    the gate the operator reads before deleting personas.db: a non-empty list means the
    platform does NOT yet hold a faithful copy, so the source of truth must not be dropped."""
    problems: list[str] = []

    for slug, want in expected_sources.items():
        got = sources_by_slug.get(slug)
        if got is None:
            problems.append(f"source {slug!r} missing from the registry")
            continue
        for label in _SOURCE_FIELDS:
            w, g = want[label], got.get(label)
            if label == "feed_urls":
                if list(w) != list(g or []):
                    problems.append(f"source {slug}: feed_urls {w!r} != {g!r}")
            elif w != g:
                problems.append(f"source {slug}: {label} {w!r} != {g!r}")

    for handle, expect in expected_authors.items():
        want, expect_deleted = expect["body"], expect["deleted"]
        got = authors_by_handle.get(handle)
        if got is None:
            problems.append(f"author {handle!r} missing from the registry")
            continue
        if bool(got.get("deleted")) != expect_deleted:
            problems.append(f"author {handle}: deleted {got.get('deleted')!r} != {expect_deleted!r}")
        for label in ("name", "bio", "avatar", "gender", "about", "style"):
            if want[label] != got.get(label):
                problems.append(f"author {handle}: {label} {want[label]!r} != {got.get(label)!r}")
        if list(want["topics"]) != list(got.get("topics") or []):
            problems.append(f"author {handle}: topics {want['topics']!r} != {got.get('topics')!r}")
        # author_sources is a SET: the backend stores + returns it sorted, so compare
        # order-independently (a persona's list order is not meaningful on the join).
        got_sources = norm_slugs(got.get("sources") or [])
        if sorted(norm_slugs(want["sources"])) != sorted(got_sources):
            problems.append(f"author {handle}: sources {sorted(norm_slugs(want['sources']))} != {sorted(got_sources)}")
        # No FK on author_sources: a slug with no live source row is a dangling reference the
        # set-comparison alone would not catch (it round-trips through the join that stored it).
        for slug in got_sources:
            if slug not in sources_by_slug:
                problems.append(f"author {handle}: source {slug!r} is attached but has no registered source row")
        # Full metadata equality (not one-directional): the wholesale-replace upsert must land
        # exactly the metadata the move sent, no more (a stray backend key) and no less.
        got_meta = got.get("metadata") or {}
        if dict(want["metadata"]) != dict(got_meta):
            problems.append(f"author {handle}: metadata {want['metadata']!r} != {got_meta!r}")

    return problems
