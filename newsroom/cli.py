"""The brain's management CLI: curate the newsroom config from the command line.

``censurado-brain <subcommand>`` is the command-line parity of the brain's HTTP
management API. It does NOT write articles (the CLI authoring agent does that and
publishes to the backend directly); it manages the newsroom's CONFIGURATION:

  * ``bootstrap``       seed a fresh box (idempotent) and mirror the platform authors
  * ``mirror-authors``  push local personas' public fields to the platform registry
  * ``status``          probe the brain<->backend connection
  * ``sources``         curate the source (portal) registry
  * ``authors``         curate the author (persona) registry + per-author source links
  * ``editorial``       read/edit the house style + publication location
  * ``topics``          remap topic tags onto an agent-supplied canonical map
  * ``embeds``          re-check stored tweet/youtube snapshots

Run it directly (``python -m newsroom <subcommand>``) or via the installed console
script (``censurado-brain <subcommand>``). Each subcommand prints a one-line JSON
result to stdout and sets an exit status a caller can branch on (0 ok, non-zero on a
failure or a partial apply).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import asdict, replace

from newsroom.bootstrap import bootstrap
from newsroom.config import Settings, load_settings
from newsroom.contracts.sections import SECTION_ENUM
from newsroom.db import open_db
from newsroom.editorial import (
    Location,
    LocationStore,
    Portal,
    PortalStore,
    StyleGuide,
    StyleStore,
)
from newsroom.mirror import (
    AuthorPush,
    BackendProbe,
    PushResult,
    backfill_web_authors,
    probe_backend,
    push_web_author,
)
from newsroom.personas import Persona, PersonaStore

__all__ = ["main"]

# Exit codes a caller can branch on. A subcommand that did not fully succeed is non-zero
# so an operator's failure alerting fires on a partial apply, not only on a crash.
_EXIT = {"done": 0, "done_with_errors": 2, "failed": 1}


def _emit(summary: dict) -> None:
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")


def _bootstrap_main(argv: list[str]) -> int:
    """``censurado-brain bootstrap``: idempotently seed the newsroom (editorial config +
    any default portals/personas) and mirror the platform authors. Safe to re-run. Prints
    a JSON summary and returns 0."""
    parser = argparse.ArgumentParser(
        prog="censurado-brain bootstrap",
        description="Seed the newsroom (idempotent) and mirror the platform authors.",
    )
    parser.parse_args(argv)  # no flags; surfaces -h and rejects stray args

    settings = load_settings()
    result = bootstrap(settings)
    _emit(result)
    return 0


def _mirror_authors_main(argv: list[str], *, push: AuthorPush | None = None) -> int:
    """``censurado-brain mirror-authors [--dry-run]``: the one-time backfill that pushes
    every local persona's public fields (handle/name/bio/avatar) to the platform author
    registry, making the platform authoritative. Idempotent (the platform upserts on
    handle). ``--dry-run`` previews the handles without contacting the platform. Prints a
    JSON summary; returns 0 when every push succeeded (or dry-run), 1 if any failed or no
    operator token is configured."""
    parser = argparse.ArgumentParser(
        prog="censurado-brain mirror-authors",
        description="Push local personas' public fields to the platform author registry.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list the handles that would be pushed; push nothing"
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    personas = PersonaStore(conn).list(include_inactive=True)

    if args.dry_run:
        report = backfill_web_authors(personas, _dry_run_push)
        report.dry_run = True
        _emit(report.as_dict())
        return 0

    if push is None:
        if not settings.operator_token:
            _emit({"pushed": [], "failed": [], "error": "no operator token configured"})
            return _EXIT["failed"]
        push = _env_author_push(settings)

    report = backfill_web_authors(personas, push)
    _emit(report.as_dict())
    return 0 if not report.failed else _EXIT["failed"]


def _dry_run_push(persona: Persona) -> PushResult:
    """A no-network push that always reports success, so --dry-run lists what WOULD be
    pushed (the handles land in the report's ``pushed``) without contacting the platform."""
    return PushResult(handle=persona.id, ok=True, status=0)


def _env_author_push(settings: Settings) -> AuthorPush:
    """The production push: upsert a persona's public fields to the platform with the
    operator key. The key must carry the admin:write scope."""

    def push(persona: Persona) -> PushResult:
        return push_web_author(
            settings.publish_base_url,
            settings.operator_token,
            handle=persona.id,
            name=persona.display_name,
            bio=persona.about,
            avatar=persona.avatar_path,
        )

    return push


# A probe takes the backend base URL + operator token and returns a typed connection
# verdict. Injected so the `status` verb tests without contacting a real backend.
BackendProbeFn = Callable[[str, str], BackendProbe]


def _status_main(argv: list[str], *, probe: BackendProbeFn | None = None) -> int:
    """``censurado-brain status``: print the brain<->backend connection diagnostic, the
    CLI parity of ``GET /status/backend``. Reads the backend base URL + operator token
    from the environment and probes the backend read API (GET /authors) with a bounded
    timeout, NEVER raising. Prints the same JSON shape the HTTP route returns (base URL,
    whether the token is configured, reachable / authorized / remote author count) via
    ``_emit``. Returns 0 when the backend is reachable AND the token is authorized, else
    1, so a health check can branch on the exit code. ``probe`` is injectable for tests."""
    parser = argparse.ArgumentParser(
        prog="censurado-brain status",
        description="Report the brain's connection to the platform backend (live probe).",
    )
    parser.parse_args(argv)  # no flags; surfaces -h and rejects stray args

    settings = load_settings()
    base_url = str(settings.publish_base_url).rstrip("/")
    probe = probe or probe_backend
    result = probe(base_url, settings.operator_token)
    _emit({
        "backend_base_url": base_url,
        "token_configured": bool(settings.operator_token),
        "reachable": result.reachable,
        "authorized": result.authorized,
        "author_count": result.author_count,
        "status_code": result.status,
        "detail": result.detail,
    })
    return 0 if (result.reachable and result.authorized) else _EXIT["failed"]


def _open_portal_store() -> PortalStore:
    """Open the brain DB the API uses and wrap it in a ``PortalStore``. The CLI source
    verbs are their own process (a separate connection from a live brain); WAL + the
    busy timeout (see ``open_db``) let that connection share the file safely."""
    settings = load_settings()
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    return PortalStore(conn)


def _split_csv(raw: str | None) -> list[str]:
    """Split a comma-separated CLI value into a clean list (``--feed-urls a,b`` -> [a, b])."""
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _sources_main(argv: list[str], *, store: PortalStore | None = None) -> int:
    """``censurado-brain sources list|get|add|update|remove|enable|disable``: curate the
    source (portal) registry from the command line, mirroring the HTTP management API.
    Operates on a local ``PortalStore`` over the brain DB. Prints a JSON result via
    ``_emit`` and returns 0 on success, 1 on a store rejection (duplicate domain, invalid
    feed_type, unknown id) or a missing/unknown sub-verb. ``store`` is injectable for tests."""
    if not argv:
        _emit({"error": "usage: sources list|get|add|update|remove|enable|disable"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("list", "get", "add", "update", "remove", "enable", "disable"):
        _emit({"error": f"unknown sources verb {verb!r}"})
        return _EXIT["failed"]

    store = store or _open_portal_store()

    if verb == "get":
        parser = argparse.ArgumentParser(prog="censurado-brain sources get")
        parser.add_argument("portal_id", help="the source id (e.g. example-com)")
        args = parser.parse_args(rest)
        portal = store.get(args.portal_id)
        if portal is None:  # the CLI parity of GET /portals/{id}'s 404
            _emit({"error": f"unknown source {args.portal_id!r}"})
            return _EXIT["failed"]
        _emit(asdict(portal))
        return 0

    if verb == "list":
        parser = argparse.ArgumentParser(prog="censurado-brain sources list")
        parser.add_argument(
            "--enabled", action=argparse.BooleanOptionalAction, default=None,
            help="filter to enabled (--enabled) or disabled (--no-enabled) sources",
        )
        args = parser.parse_args(rest)
        portals = store.list(enabled=args.enabled)
        _emit({"portals": [asdict(p) for p in portals], "total": len(portals)})
        return 0

    if verb == "add":
        parser = argparse.ArgumentParser(prog="censurado-brain sources add")
        parser.add_argument("--domain", required=True, help="the source domain or URL")
        parser.add_argument("--homepage", default="")
        parser.add_argument("--description", default="", help="operator note on the source")
        parser.add_argument("--feed-urls", default=None, help="comma-separated known feed URLs")
        parser.add_argument("--feed-type", default="auto")
        parser.add_argument("--language", default="es")
        parser.add_argument("--ownership-group", default="")
        parser.add_argument(
            "--disabled", action="store_true", help="add the source disabled (default: enabled)"
        )
        args = parser.parse_args(rest)
        portal = Portal(
            domain=args.domain,
            homepage=args.homepage,
            description=args.description,
            feed_urls=_split_csv(args.feed_urls),
            feed_type=args.feed_type,
            language=args.language,
            ownership_group=args.ownership_group,
            enabled=not args.disabled,
        )
        try:
            stored = store.create(portal)
        except ValueError as exc:
            _emit({"error": str(exc)})
            return _EXIT["failed"]
        _emit(asdict(stored))
        return 0

    if verb == "update":
        parser = argparse.ArgumentParser(prog="censurado-brain sources update")
        parser.add_argument("portal_id", help="the source id (e.g. example-com)")
        parser.add_argument("--homepage", default=None)
        parser.add_argument("--description", default=None)
        parser.add_argument("--feed-urls", default=None, help="comma-separated; replaces the list")
        parser.add_argument("--feed-type", default=None)
        parser.add_argument("--language", default=None)
        parser.add_argument("--ownership-group", default=None)
        parser.add_argument(
            "--enabled", action=argparse.BooleanOptionalAction, default=None,
            help="enable (--enabled) or disable (--no-enabled) the source",
        )
        args = parser.parse_args(rest)
        changes: dict = {}
        if args.homepage is not None:
            changes["homepage"] = args.homepage
        if args.description is not None:
            changes["description"] = args.description
        if args.feed_urls is not None:
            changes["feed_urls"] = _split_csv(args.feed_urls)
        if args.feed_type is not None:
            changes["feed_type"] = args.feed_type
        if args.language is not None:
            changes["language"] = args.language
        if args.ownership_group is not None:
            changes["ownership_group"] = args.ownership_group
        if args.enabled is not None:
            changes["enabled"] = args.enabled
        try:
            stored = store.update(args.portal_id, **changes)
        except KeyError:
            _emit({"error": f"unknown source {args.portal_id!r}"})
            return _EXIT["failed"]
        except ValueError as exc:
            _emit({"error": str(exc)})
            return _EXIT["failed"]
        _emit(asdict(stored))
        return 0

    if verb in ("enable", "disable"):
        parser = argparse.ArgumentParser(prog=f"censurado-brain sources {verb}")
        parser.add_argument("portal_id", help="the source id (e.g. example-com)")
        args = parser.parse_args(rest)
        try:
            stored = store.set_enabled(args.portal_id, verb == "enable")
        except KeyError:
            _emit({"error": f"unknown source {args.portal_id!r}"})
            return _EXIT["failed"]
        _emit(asdict(stored))
        return 0

    # verb == "remove"
    parser = argparse.ArgumentParser(prog="censurado-brain sources remove")
    parser.add_argument("portal_id", help="the source id (e.g. example-com)")
    args = parser.parse_args(rest)
    removed = store.delete(args.portal_id)
    _emit({"id": args.portal_id, "removed": removed})
    return 0 if removed else _EXIT["failed"]


# A topics-cleanse seam bundle, injected by tests so the verb is driven end to end
# without a network (fetch the corpus, apply the remap). In production each defaults to
# the real implementation over the live APIs.
TopicsFetch = Callable[[], list]
TopicsApply = Callable[[list], tuple]


def _topics_main(
    argv: list[str],
    *,
    fetch: TopicsFetch | None = None,
    apply: TopicsApply | None = None,
) -> int:
    """``censurado-brain topics cleanse --map-file PATH [--apply]``: remap topic tags onto
    a canonical set. Reads the corpus tag set over the read API, applies the canonical
    ``{tag: canonical}`` map an agent supplies via ``--map-file`` (a file path, or ``-`` for
    stdin), and (with ``--apply``) remaps each changed article in place over the operator
    edit lane (``PUT /articles``, admin:write). Without ``--map-file`` every tag maps to
    itself (a no-op). Default is a DRY-RUN that prints the canonical map + the per-article
    plan and writes nothing. Prints a JSON summary via ``_emit``; returns 0 on success, 2 if
    some applies failed, 1 on a read/usage error. The fetch + apply seams are injectable so
    a test drives the verb without a network."""
    if not argv or argv[0] != "cleanse":
        _emit({"error": "usage: topics cleanse --map-file PATH [--apply]"})
        return _EXIT["failed"]
    parser = argparse.ArgumentParser(prog="censurado-brain topics cleanse")
    parser.add_argument(
        "--apply", action="store_true",
        help="apply the remap (default: dry-run, print the plan and write nothing)",
    )
    parser.add_argument(
        "--map-file", default=None, metavar="PATH",
        help="the canonical map (JSON {tag: canonical}) from PATH (or '-' for stdin). This "
        "is how a CLI agent supplies the clustering itself, with no inference backend in "
        "the loop. Omit to map every tag to itself (a no-op).",
    )
    args = parser.parse_args(argv[1:])

    from newsroom.cleanse import apply_remap, collect_topics, fetch_articles, remap_plan

    settings = load_settings()
    base = str(settings.publish_base_url).rstrip("/")
    read_token = settings.operator_token
    edit_token = settings.admin_token or settings.operator_token

    fetch = fetch or (lambda: fetch_articles(base, read_token))
    apply = apply or (lambda plan: apply_remap(plan, base_url=base, read_token=read_token, edit_token=edit_token))

    try:
        articles = fetch()
    except Exception as exc:  # a read failure is a clean usage error, not a crash
        _emit({"error": f"failed to read the corpus: {exc}"})
        return _EXIT["failed"]

    tags = collect_topics(articles)
    # The agent-supplied path: read a {tag: canonical} map (file or '-' stdin) and use it
    # directly, so the clustering comes from a CLI agent rather than a backend model. Every
    # corpus tag maps to itself unless the map overrides it; with no map, the whole pass is
    # a no-op (identity).
    raw: dict = {}
    if args.map_file:
        try:
            src = sys.stdin.read() if args.map_file == "-" else open(args.map_file, encoding="utf-8").read()
            raw = json.loads(src)
            if not isinstance(raw, dict):
                raise ValueError("map must be a JSON object {tag: canonical}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _emit({"error": f"failed to read --map-file: {exc}"})
            return _EXIT["failed"]
    canon = {t: (str(raw.get(t, t)).strip() or t) for t in tags}
    plan = remap_plan(articles, canon)
    summary = {
        "tags_before": len(tags),
        "tags_after": len({c for c in canon.values()}),
        "articles_changed": len(plan),
        "applied": 0,
        "failed": [],
        "dry_run": not args.apply,
        "mapping": canon,
        "plan": [{"slug": rm.slug, "before": rm.before, "after": rm.after} for rm in plan],
    }
    if args.apply:
        try:
            applied, failed = apply(plan)
        except Exception as exc:
            _emit({"error": f"apply failed: {exc}"})
            return _EXIT["failed"]
        summary["applied"] = applied
        summary["failed"] = failed
    _emit(summary)
    return _EXIT["done_with_errors"] if summary["failed"] else _EXIT["done"]


# An embeds-recheck seam bundle, injected by tests so the verb is driven end to end
# without a network: list the corpus, recheck each article's snapshots, apply the changes.
# In production each defaults to the real implementation over the live APIs.
EmbedsList = Callable[[], list]
EmbedsApply = Callable[[list, bool], tuple]


def _embeds_main(
    argv: list[str],
    *,
    list_slugs: EmbedsList | None = None,
    apply: EmbedsApply | None = None,
) -> int:
    """``censurado-brain embeds recheck [--apply]``: re-validate every stored tweet/youtube
    snapshot and refresh its availability flag. A deleted tweet flips to ``erased`` (its
    captured text is kept), a pulled video flips ``available`` to false, so the site then
    renders the "eliminado" states. Default is a DRY-RUN that counts what would change and
    writes nothing; ``--apply`` writes the changed articles back over the operator edit lane
    (``PUT /articles``, admin:write). The two seams are injectable so a test drives the verb
    without a network."""
    if not argv or argv[0] != "recheck":
        _emit({"error": "usage: embeds recheck [--apply]"})
        return _EXIT["failed"]
    parser = argparse.ArgumentParser(prog="censurado-brain embeds recheck")
    parser.add_argument(
        "--apply", action="store_true",
        help="write back the changed articles (default: dry-run, only count what would change)",
    )
    args = parser.parse_args(argv[1:])

    from newsroom.embeds import apply_rechecks, fetch_article_slugs

    settings = load_settings()
    base = str(settings.publish_base_url).rstrip("/")
    read_token = settings.operator_token
    edit_token = settings.admin_token or settings.operator_token

    list_slugs = list_slugs or (lambda: fetch_article_slugs(base, read_token))
    apply = apply or (
        lambda slugs, do: apply_rechecks(
            slugs, base_url=base, read_token=read_token, edit_token=edit_token, apply=do
        )
    )

    try:
        slugs = list_slugs()
    except Exception as exc:  # a read failure is a clean usage error, not a crash
        _emit({"error": f"failed to read the corpus: {exc}"})
        return _EXIT["failed"]

    try:
        changed, applied, failed = apply(slugs, args.apply)
    except Exception as exc:
        _emit({"error": f"recheck failed: {exc}"})
        return _EXIT["failed"]

    _emit({
        "scanned": len(slugs),
        "changed": changed,
        "applied": applied,
        "failed": failed,
        "dry_run": not args.apply,
    })
    return _EXIT["done_with_errors"] if failed else _EXIT["done"]


def _open_persona_store() -> PersonaStore:
    """Open the brain DB the API uses and wrap it in a ``PersonaStore``. The CLI author
    verbs are their own process (a separate connection from a live brain); WAL + the busy
    timeout (see ``open_db``) let that connection share the file safely."""
    settings = load_settings()
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    return PersonaStore(conn)


def _persona_sources_payload(persona: Persona, portal_store: PortalStore) -> dict:
    """The author's source pool as a JSON-able dict, mirroring the HTTP
    ``PersonaSourcesOut``: the raw linked ids (``sources`` -- the same key the persona
    carries everywhere) plus those resolved to the live portal rows (a stale id whose
    portal is gone stays in ``sources`` but not ``portals``)."""
    portals = [
        asdict(portal)
        for portal in (portal_store.get(pid) for pid in persona.sources)
        if portal is not None
    ]
    return {
        "persona_id": persona.id,
        "sources": list(persona.sources),
        "portals": portals,
    }


def _authors_sources_main(
    argv: list[str], *, persona_store: PersonaStore, portal_store: PortalStore | None
) -> int:
    """``censurado-brain authors sources get|set|add|remove <persona_id> ...``: curate an
    author's per-author source pool (the link set), the CLI parity of the HTTP linking
    surface. ``set`` REPLACES the pool from ``--sources`` (validating each id against the
    portal registry; an unknown id -> error, no write); ``add``/``remove`` link/unlink one
    (idempotent), with ``add`` rejecting an unknown portal. Prints the resolved pool via
    ``_emit`` and returns 0 on success, 1 on a missing author/portal or unknown sub-verb."""
    if not argv:
        _emit({"error": "usage: authors sources get|set|add|remove"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("get", "set", "add", "remove"):
        _emit({"error": f"unknown authors sources verb {verb!r}"})
        return _EXIT["failed"]

    portal_store = portal_store or _open_portal_store()

    if verb == "get":
        parser = argparse.ArgumentParser(prog="censurado-brain authors sources get")
        parser.add_argument("persona_id", help="the persona id (e.g. ada-lovelace)")
        args = parser.parse_args(rest)
        persona = persona_store.get(args.persona_id)
        if persona is None:
            _emit({"error": f"unknown author {args.persona_id!r}"})
            return _EXIT["failed"]
        _emit(_persona_sources_payload(persona, portal_store))
        return 0

    if verb == "set":
        parser = argparse.ArgumentParser(prog="censurado-brain authors sources set")
        parser.add_argument("persona_id", help="the persona id (e.g. ada-lovelace)")
        parser.add_argument(
            "--sources", default="", help="comma-separated portal ids; REPLACES the link set"
        )
        args = parser.parse_args(rest)
        persona = persona_store.get(args.persona_id)
        if persona is None:
            _emit({"error": f"unknown author {args.persona_id!r}"})
            return _EXIT["failed"]
        ids: list[str] = []
        seen: set[str] = set()
        for pid in _split_csv(args.sources):
            if pid not in seen:
                seen.add(pid)
                ids.append(pid)
        unknown = [pid for pid in ids if portal_store.get(pid) is None]
        if unknown:
            _emit({"error": f"unknown source id(s): {unknown}"})
            return _EXIT["failed"]
        stored = persona_store.update(args.persona_id, sources=ids)
        _emit(_persona_sources_payload(stored, portal_store))
        return 0

    # verb in ("add", "remove"): link/unlink one portal id.
    parser = argparse.ArgumentParser(prog=f"censurado-brain authors sources {verb}")
    parser.add_argument("persona_id", help="the persona id (e.g. ada-lovelace)")
    parser.add_argument("portal_id", help="the source id (e.g. example-com)")
    args = parser.parse_args(rest)
    persona = persona_store.get(args.persona_id)
    if persona is None:
        _emit({"error": f"unknown author {args.persona_id!r}"})
        return _EXIT["failed"]

    if verb == "add":
        if portal_store.get(args.portal_id) is None:
            _emit({"error": f"unknown source {args.portal_id!r}"})
            return _EXIT["failed"]
        if args.portal_id not in persona.sources:  # idempotent add
            persona = persona_store.update(
                args.persona_id, sources=[*persona.sources, args.portal_id]
            )
        _emit(_persona_sources_payload(persona, portal_store))
        return 0

    # verb == "remove": idempotent unlink (a missing portal is not an error here).
    if args.portal_id in persona.sources:
        remaining = [pid for pid in persona.sources if pid != args.portal_id]
        persona = persona_store.update(args.persona_id, sources=remaining)
    _emit(_persona_sources_payload(persona, portal_store))
    return 0


def _authors_main(
    argv: list[str],
    *,
    store: PersonaStore | None = None,
    portal_store: PortalStore | None = None,
) -> int:
    """``censurado-brain authors list|get|add|update|remove|sources``: curate the author
    (persona) registry from the command line, mirroring the HTTP management API. ``add``
    builds a persona from EXPLICIT fields with no model call (POST /personas/direct).
    Operates on a local ``PersonaStore`` over the brain DB. Prints a JSON result via
    ``_emit`` and returns 0 on success, 1 on a store rejection (duplicate id, invalid beat,
    unknown id, an in-use delete), or a missing/unknown sub-verb. The ``sources`` sub-verb
    curates an author's source links and also needs the ``PortalStore`` to validate ids. The
    stores are injectable for tests."""
    if not argv:
        _emit({"error": "usage: authors list|get|add|update|remove|sources"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("list", "get", "add", "update", "remove", "sources"):
        _emit({"error": f"unknown authors verb {verb!r}"})
        return _EXIT["failed"]

    store = store or _open_persona_store()

    if verb == "sources":
        return _authors_sources_main(rest, persona_store=store, portal_store=portal_store)

    if verb == "list":
        parser = argparse.ArgumentParser(prog="censurado-brain authors list")
        parser.add_argument("--beat", default=None, help=f"filter to one beat ({', '.join(SECTION_ENUM)})")
        parser.add_argument(
            "--include-inactive", action="store_true",
            help="include soft-deactivated personas (default: active only)",
        )
        args = parser.parse_args(rest)
        personas = store.list(beat=args.beat, include_inactive=args.include_inactive)
        _emit({"personas": [asdict(p) for p in personas], "total": len(personas)})
        return 0

    if verb == "get":
        parser = argparse.ArgumentParser(prog="censurado-brain authors get")
        parser.add_argument("persona_id", help="the persona id (e.g. ada-lovelace)")
        args = parser.parse_args(rest)
        persona = store.get(args.persona_id)
        if persona is None:
            _emit({"error": f"unknown author {args.persona_id!r}"})
            return _EXIT["failed"]
        _emit(asdict(persona))
        return 0

    if verb == "add":
        parser = argparse.ArgumentParser(prog="censurado-brain authors add")
        parser.add_argument("--display-name", required=True, help="the author's display name")
        parser.add_argument("--beat", required=True, help=f"one of {', '.join(SECTION_ENUM)}")
        parser.add_argument("--who-i-am", required=True, help="the persona's first-person identity")
        parser.add_argument("--style", required=True, help="the persona's writing style")
        parser.add_argument(
            "--id", default="", help="explicit id (default: derived from display-name)"
        )
        parser.add_argument("--about", default="", help="the public bio")
        parser.add_argument("--language", default="español neutro")
        parser.add_argument("--sources", default=None, help="comma-separated source ids/urls")
        parser.add_argument("--avatar-path", default="")
        parser.add_argument(
            "--inactive", action="store_true", help="add the author soft-deactivated (default: active)"
        )
        args = parser.parse_args(rest)
        persona = Persona(
            display_name=args.display_name,
            beat=args.beat,
            who_i_am=args.who_i_am,
            style=args.style,
            id=args.id,
            about=args.about,
            language=args.language,
            sources=_split_csv(args.sources),
            avatar_path=args.avatar_path,
            active=not args.inactive,
        )
        try:
            stored = store.create(persona)
        except ValueError as exc:
            _emit({"error": str(exc)})
            return _EXIT["failed"]
        _emit(asdict(stored))
        return 0

    if verb == "update":
        parser = argparse.ArgumentParser(prog="censurado-brain authors update")
        parser.add_argument("persona_id", help="the persona id (e.g. ada-lovelace)")
        parser.add_argument("--display-name", default=None)
        parser.add_argument("--beat", default=None, help=f"one of {', '.join(SECTION_ENUM)}")
        parser.add_argument("--who-i-am", default=None)
        parser.add_argument("--about", default=None)
        parser.add_argument("--style", default=None)
        parser.add_argument("--language", default=None)
        parser.add_argument("--sources", default=None, help="comma-separated; replaces the list")
        parser.add_argument("--avatar-path", default=None)
        parser.add_argument(
            "--active", action=argparse.BooleanOptionalAction, default=None,
            help="activate (--active) or soft-deactivate (--no-active) the author",
        )
        args = parser.parse_args(rest)
        changes: dict = {}
        if args.display_name is not None:
            changes["display_name"] = args.display_name
        if args.beat is not None:
            changes["beat"] = args.beat
        if args.who_i_am is not None:
            changes["who_i_am"] = args.who_i_am
        if args.about is not None:
            changes["about"] = args.about
        if args.style is not None:
            changes["style"] = args.style
        if args.language is not None:
            changes["language"] = args.language
        if args.sources is not None:
            changes["sources"] = _split_csv(args.sources)
        if args.avatar_path is not None:
            changes["avatar_path"] = args.avatar_path
        if args.active is not None:
            changes["active"] = args.active
        try:
            stored = store.update(args.persona_id, **changes)
        except KeyError:
            _emit({"error": f"unknown author {args.persona_id!r}"})
            return _EXIT["failed"]
        except ValueError as exc:
            _emit({"error": str(exc)})
            return _EXIT["failed"]
        _emit(asdict(stored))
        return 0

    # verb == "remove"
    parser = argparse.ArgumentParser(prog="censurado-brain authors remove")
    parser.add_argument("persona_id", help="the persona id (e.g. ada-lovelace)")
    args = parser.parse_args(rest)
    try:
        removed = store.delete(args.persona_id)
    except ValueError as exc:  # referenced by an assignment: a conflict, not a clean delete
        _emit({"error": str(exc)})
        return _EXIT["failed"]
    _emit({"id": args.persona_id, "removed": removed})
    return 0 if removed else _EXIT["failed"]


# ----- editorial config (house style + location) -----

# The full-guide content fields an operator may set over the CLI. Read-only metadata the
# store assigns (version / created_at / is_active) is ignored on `style set`, so the JSON
# `style get` prints can be edited and fed straight back in.
_STYLE_CONTENT_FIELDS = ("voice", "exemplars", "rules", "lexicon", "sourcing", "structure")


def _open_style_store() -> StyleStore:
    """Open the brain DB the API uses and wrap it in a ``StyleStore``. The CLI editorial
    verbs are their own process (a separate connection from a live brain); WAL + the busy
    timeout (see ``open_db``) let that connection share the file safely."""
    settings = load_settings()
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    return StyleStore(conn)


def _open_location_store() -> LocationStore:
    """Open the brain DB the API uses and wrap it in a ``LocationStore`` (same sharing
    story as ``_open_style_store``)."""
    settings = load_settings()
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    return LocationStore(conn)


def _location_payload(location: Location) -> dict:
    """A ``Location`` as a JSON-able dict, including the derived Google-News views
    (``gl`` / ``hl`` / ``ceid``), mirroring the HTTP ``LocationOut``."""
    return {
        "region": location.region,
        "ui_lang": location.ui_lang,
        "language": location.language,
        "gdelt_country": location.gdelt_country,
        "city": location.city,
        "latlong": location.latlong,
        "updated_at": location.updated_at,
        "gl": location.gl,
        "hl": location.hl,
        "ceid": location.ceid,
    }


def _parse_json_obj(raw: str) -> dict | None:
    """Parse ``raw`` as a JSON object; return None (the caller emits the error) when it is
    not valid JSON or not an object."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _editorial_style_main(argv: list[str], *, style_store: StyleStore) -> int:
    """``censurado-brain editorial style get|set|versions|promote``: read and edit the
    VERSIONED house style. ``get`` prints the active version; ``set --json`` publishes a
    NEW version (activated) from a full-guide JSON document; ``versions`` lists the audit
    trail newest-first; ``promote <version>`` makes a version active (also how a rollback
    works). Prints JSON via ``_emit``; returns 0 on success, 1 on a bad document / no
    active version / unknown version / unknown sub-verb."""
    if not argv:
        _emit({"error": "usage: editorial style get|set|versions|promote"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("get", "set", "versions", "promote"):
        _emit({"error": f"unknown editorial style verb {verb!r}"})
        return _EXIT["failed"]

    if verb == "get":
        guide = style_store.active()
        if guide is None:
            _emit({"error": "no active style guide"})
            return _EXIT["failed"]
        _emit(asdict(guide))
        return 0

    if verb == "versions":
        versions = style_store.list_versions()
        _emit({"versions": [asdict(g) for g in versions], "total": len(versions)})
        return 0

    if verb == "set":
        parser = argparse.ArgumentParser(prog="censurado-brain editorial style set")
        parser.add_argument("--json", required=True, help="the full style guide as a JSON object")
        parser.add_argument("--created-by", default="", help="author note recorded on the version")
        parser.add_argument(
            "--no-activate", action="store_true",
            help="stage the version without making it active (default: activate)",
        )
        args = parser.parse_args(rest)
        doc = _parse_json_obj(args.json)
        if doc is None:
            _emit({"error": "--json must be a JSON object"})
            return _EXIT["failed"]
        fields = {k: doc[k] for k in _STYLE_CONTENT_FIELDS if k in doc}
        stored = style_store.add_version(
            StyleGuide(**fields),
            created_by=args.created_by or str(doc.get("created_by", "")),
            activate=not args.no_activate,
        )
        _emit(asdict(stored))
        return 0

    # verb == "promote"
    parser = argparse.ArgumentParser(prog="censurado-brain editorial style promote")
    parser.add_argument("version", type=int, help="the style version to make active")
    args = parser.parse_args(rest)
    try:
        promoted = style_store.promote(args.version)
    except KeyError:
        _emit({"error": f"no style version {args.version}"})
        return _EXIT["failed"]
    _emit(asdict(promoted))
    return 0


def _editorial_lexicon_main(argv: list[str], *, style_store: StyleStore) -> int:
    """``censurado-brain editorial lexicon get|set``: read/replace the banned-term lexicon
    of the active style. ``set`` derives a NEW active version (the rest of the guide is
    carried over): ``--json`` replaces the whole lexicon object, ``--banned-terms`` (csv)
    replaces just the banned list on the active lexicon. Returns 0 on success, 1 on no
    active guide / bad input / unknown sub-verb."""
    if not argv:
        _emit({"error": "usage: editorial lexicon get|set"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("get", "set"):
        _emit({"error": f"unknown editorial lexicon verb {verb!r}"})
        return _EXIT["failed"]

    active = style_store.active()
    if active is None:
        _emit({"error": "no active style guide"})
        return _EXIT["failed"]

    if verb == "get":
        _emit(active.lexicon or {})
        return 0

    # verb == "set"
    parser = argparse.ArgumentParser(prog="censurado-brain editorial lexicon set")
    parser.add_argument("--json", default=None, help="the full lexicon as a JSON object")
    parser.add_argument(
        "--banned-terms", default=None,
        help="comma-separated banned terms; replaces banned_terms on the active lexicon",
    )
    args = parser.parse_args(rest)
    if args.json is None and args.banned_terms is None:
        _emit({"error": "provide --json or --banned-terms"})
        return _EXIT["failed"]
    if args.json is not None:
        lexicon = _parse_json_obj(args.json)
        if lexicon is None:
            _emit({"error": "--json must be a JSON object"})
            return _EXIT["failed"]
    else:
        lexicon = dict(active.lexicon or {})
    if args.banned_terms is not None:
        lexicon["banned_terms"] = _split_csv(args.banned_terms)
    stored = style_store.add_version(
        replace(active, lexicon=lexicon), created_by=active.created_by, activate=True
    )
    _emit(stored.lexicon or {})
    return 0


def _editorial_sourcing_main(argv: list[str], *, style_store: StyleStore) -> int:
    """``censurado-brain editorial sourcing get|set``: read/edit the sourcing block of the
    active style (the ``min_sources`` corroboration floor plus the attribution flags).
    ``set`` derives a NEW active version with the named knobs MERGED onto the active
    sourcing, so ``--min-sources 3`` keeps the other flags. ``--json`` replaces the whole
    block first, then the flags override. Returns 0 on success, 1 on no active guide / bad
    input / unknown sub-verb."""
    if not argv:
        _emit({"error": "usage: editorial sourcing get|set"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("get", "set"):
        _emit({"error": f"unknown editorial sourcing verb {verb!r}"})
        return _EXIT["failed"]

    active = style_store.active()
    if active is None:
        _emit({"error": "no active style guide"})
        return _EXIT["failed"]

    if verb == "get":
        _emit(active.sourcing or {})
        return 0

    # verb == "set"
    parser = argparse.ArgumentParser(prog="censurado-brain editorial sourcing set")
    parser.add_argument("--json", default=None, help="the full sourcing block as a JSON object")
    parser.add_argument("--min-sources", type=int, default=None, help="the corroboration floor")
    parser.add_argument(
        "--require-attribution", action=argparse.BooleanOptionalAction, default=None,
        help="require every claim attributed to a named source",
    )
    parser.add_argument(
        "--no-fabricated-quotes", action=argparse.BooleanOptionalAction, default=None,
        dest="no_fabricated_quotes", help="forbid fabricated quotes",
    )
    args = parser.parse_args(rest)
    if args.json is not None:
        sourcing = _parse_json_obj(args.json)
        if sourcing is None:
            _emit({"error": "--json must be a JSON object"})
            return _EXIT["failed"]
    else:
        sourcing = dict(active.sourcing or {})
    if args.min_sources is not None:
        sourcing["min_sources"] = args.min_sources
    if args.require_attribution is not None:
        sourcing["require_attribution"] = args.require_attribution
    if args.no_fabricated_quotes is not None:
        sourcing["no_fabricated_quotes"] = args.no_fabricated_quotes
    if args.json is None and args.min_sources is None and args.require_attribution is None \
            and args.no_fabricated_quotes is None:
        _emit({"error": "provide --json or at least one sourcing flag"})
        return _EXIT["failed"]
    stored = style_store.add_version(
        replace(active, sourcing=sourcing), created_by=active.created_by, activate=True
    )
    _emit(stored.sourcing or {})
    return 0


def _editorial_location_main(argv: list[str], *, location_store: LocationStore) -> int:
    """``censurado-brain editorial location get|set``: read/upsert the single publication
    location row. ``get`` always returns a usable value (the default until one is stored);
    ``set`` applies only the named flags over the current value. Returns 0 on success, 1 on
    a store rejection (unknown field / blank required field) / nothing to set / unknown
    sub-verb."""
    if not argv:
        _emit({"error": "usage: editorial location get|set"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("get", "set"):
        _emit({"error": f"unknown editorial location verb {verb!r}"})
        return _EXIT["failed"]

    if verb == "get":
        _emit(_location_payload(location_store.get()))
        return 0

    # verb == "set"
    parser = argparse.ArgumentParser(prog="censurado-brain editorial location set")
    parser.add_argument("--region", default=None, help="ISO-3166-1 alpha-2 (e.g. AR)")
    parser.add_argument("--ui-lang", default=None, help="BCP47 UI language (e.g. es-419)")
    parser.add_argument("--language", default=None, help="ISO-639-1 search language (e.g. es)")
    parser.add_argument("--gdelt-country", default=None, help="FIPS-10-4 country (e.g. AR)")
    parser.add_argument("--city", default=None)
    parser.add_argument("--latlong", default=None)
    args = parser.parse_args(rest)
    changes: dict = {}
    if args.region is not None:
        changes["region"] = args.region
    if args.ui_lang is not None:
        changes["ui_lang"] = args.ui_lang
    if args.language is not None:
        changes["language"] = args.language
    if args.gdelt_country is not None:
        changes["gdelt_country"] = args.gdelt_country
    if args.city is not None:
        changes["city"] = args.city
    if args.latlong is not None:
        changes["latlong"] = args.latlong
    if not changes:
        _emit({"error": "provide at least one field to set"})
        return _EXIT["failed"]
    try:
        stored = location_store.set(**changes)
    except ValueError as exc:
        _emit({"error": str(exc)})
        return _EXIT["failed"]
    _emit(_location_payload(stored))
    return 0


def _editorial_main(
    argv: list[str],
    *,
    style_store: StyleStore | None = None,
    location_store: LocationStore | None = None,
) -> int:
    """``censurado-brain editorial style|lexicon|sourcing|location ...``: read and edit the
    operator-owned editorial config from the command line, mirroring the HTTP editorial
    API. ``style`` / ``lexicon`` / ``sourcing`` operate on a ``StyleStore`` (the versioned
    house style); ``location`` on a ``LocationStore``. Both stores are injectable for
    tests; they default to fresh connections over the brain DB."""
    if not argv:
        _emit({"error": "usage: editorial style|lexicon|sourcing|location"})
        return _EXIT["failed"]
    group, rest = argv[0], argv[1:]
    if group not in ("style", "lexicon", "sourcing", "location"):
        _emit({"error": f"unknown editorial group {group!r}"})
        return _EXIT["failed"]

    if group == "location":
        return _editorial_location_main(rest, location_store=location_store or _open_location_store())

    style_store = style_store or _open_style_store()
    if group == "style":
        return _editorial_style_main(rest, style_store=style_store)
    if group == "lexicon":
        return _editorial_lexicon_main(rest, style_store=style_store)
    return _editorial_sourcing_main(rest, style_store=style_store)


def main(
    argv: list[str] | None = None,
    *,
    portal_store: PortalStore | None = None,
    persona_store: PersonaStore | None = None,
    style_store: StyleStore | None = None,
    location_store: LocationStore | None = None,
    backend_probe: BackendProbeFn | None = None,
) -> int:
    """Dispatch a management subcommand, print a JSON result, return an exit code.

    The stores and the backend probe default to production; a test injects doubles to
    drive a subcommand without touching the brain DB or the platform."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "bootstrap":
        # Seed the newsroom (idempotent) and mirror the platform authors.
        return _bootstrap_main(argv[1:])
    if argv and argv[0] == "mirror-authors":
        # The one-time author backfill: push local personas to the platform registry.
        return _mirror_authors_main(argv[1:])
    if argv and argv[0] == "status":
        # Report the brain<->backend connection (live probe), the CLI parity of
        # GET /status/backend.
        return _status_main(argv[1:], probe=backend_probe)
    if argv and argv[0] == "sources":
        # Curate the source (portal) registry, mirroring the HTTP management API.
        return _sources_main(argv[1:], store=portal_store)
    if argv and argv[0] == "authors":
        # Curate the author (persona) registry, mirroring the HTTP management API. The
        # portal store rides along so `authors sources` can validate source ids.
        return _authors_main(argv[1:], store=persona_store, portal_store=portal_store)
    if argv and argv[0] == "editorial":
        # Read and edit the operator-owned editorial config (house style + location),
        # mirroring the HTTP editorial API.
        return _editorial_main(
            argv[1:], style_store=style_store, location_store=location_store
        )
    if argv and argv[0] == "topics":
        # Remap topic tags onto an agent-supplied canonical map.
        return _topics_main(argv[1:])
    if argv and argv[0] == "embeds":
        # Re-check stored tweet/youtube snapshots and refresh their availability flags
        # (deleted tweet -> erased, pulled video -> unavailable).
        return _embeds_main(argv[1:])
    _emit({"error": "usage: bootstrap|mirror-authors|status|sources|authors|editorial|topics|embeds"})
    return _EXIT["failed"]
