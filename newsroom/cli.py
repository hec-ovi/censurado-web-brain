"""The automation entry point: one command that runs the brain once and exits.

This is the command an external periodic trigger (the automation layer) invokes to
produce a batch of articles. The brain stays TRIGGER-BLIND: this command does
nothing but pick a ``mode`` (the entire trigger surface) and funnel it through the
SAME run-execution path every other trigger uses (``newsroom.runner``). The brain
has no knowledge of what drives it on a schedule; swapping the automation layer
never touches the brain (architecture doc B.7).

Run it directly (``python -m newsroom --mode managed``) or via the installed console
script (``censurado-brain --mode managed``). It prints a one-line JSON summary of the
run to stdout and sets an exit status the trigger can branch on:

  * 0  the run finished and every finalized article published (``done``)
  * 2  the run finished but at least one finalized article failed to publish
       (``done_with_errors``); those articles are kept and stay re-publishable
  * 1  the run itself failed with an unexpected error (recorded ``failed``)

A non-zero status is deliberate so the trigger's own failure mail/alerting fires. A
``done`` run (exit 0) can still include articles the pipeline DROPPED (budget
exhausted, finalize failed); the summary's ``dropped`` count surfaces them, since a
dropped article published nothing but is not a publish failure either.

Dependencies are assembled from the environment by default (the same production
assembly the HTTP surface uses, in ``newsroom.runner.deps``); a test injects a
``build_deps`` that points the two network seams (web search + research) at an
in-process fake, so the real entry point is exercised without leaving the box.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict

from newsroom.bootstrap import bootstrap
from newsroom.config import Settings, load_settings
from newsroom.contracts.sections import SECTION_ENUM
from newsroom.db import open_db
from newsroom.editorial import Portal, PortalStore
from newsroom.mirror import (
    AuthorPush,
    PushResult,
    backfill_web_authors,
    push_web_author,
)
from newsroom.personas import Persona, PersonaStore
from newsroom.runner import (
    RUN_MODES,
    RunDeps,
    RunReport,
    build_run_deps,
    execute_run,
    run_direct,
    start_run,
)
from newsroom.runs import RunStore

__all__ = ["main", "build_deps_from_env"]

# Exit codes the trigger can branch on. A run that did not fully publish is non-zero
# so an operator's failure alerting fires on a partial run, not only on a crash.
_EXIT = {"done": 0, "done_with_errors": 2, "failed": 1}

DepsBuilder = Callable[[Settings], RunDeps]


def build_deps_from_env(settings: Settings) -> RunDeps:
    """Assemble run dependencies from settings + the environment over a fresh
    file-backed connection. This one-shot command is its own process: it opens its
    own connection (shared between the run path and the publish tail under one lock)
    and the production research/search seams the HTTP surface also uses."""
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    persona_store = PersonaStore(conn)
    return build_run_deps(settings, conn=conn, lock=threading.Lock(), persona_store=persona_store)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="censurado-brain",
        description="Run the newsroom once and exit (the automation entry point).",
    )
    parser.add_argument(
        "--mode",
        choices=RUN_MODES,
        default="managed",
        help="the trigger surface (default: managed, the full automated run)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=None,
        help="override the article ceiling for this run (clamped to N_MAX)",
    )
    parser.add_argument(
        "--persona-ids",
        default=None,
        help="comma-separated persona ids to draw from (default: all personas)",
    )
    parser.add_argument(
        "--images",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="generate hero images for this run (--no-images to skip); "
        "default: the server setting NEWSROOM_AUTO_GENERATE_IMAGE",
    )
    return parser


def _parse_persona_ids(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    ids = [piece.strip() for piece in raw.split(",")]
    return [pid for pid in ids if pid] or None


def _summary(report: RunReport) -> dict:
    """The machine-readable run summary written to stdout: enough for the trigger to
    log/alert on without parsing the full report. ``dropped`` is derived from the
    pipeline outcomes (not the publish outcomes) so the count stays self-consistent:
    ``assigned == published + failed + dropped`` over the finalize-or-drop set, and a
    run that drafted articles but dropped them all is not a silent zero-output green."""
    return {
        "run_id": report.run_id,
        "mode": report.mode,
        "status": report.status,
        "assigned": len(report.manifest.assignments),
        "published": sum(1 for outcome in report.published if outcome.ok),
        "failed": sum(1 for outcome in report.published if not outcome.ok),
        "dropped": sum(1 for outcome in report.outcomes if outcome.status == "dropped"),
    }


def _emit(summary: dict) -> None:
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")


def _bootstrap_main(argv: list[str]) -> int:
    """``censurado-brain bootstrap``: idempotently seed the newsroom and (by default)
    run one batch. Safe to re-run; ``--no-run`` seeds only. Prints a JSON summary and
    returns the run's exit code (0 when seed-only)."""
    parser = argparse.ArgumentParser(
        prog="censurado-brain bootstrap",
        description="Seed the newsroom (idempotent) and run one batch. Safe to re-run.",
    )
    parser.add_argument("--no-run", action="store_true", help="seed only; do not run a batch")
    parser.add_argument(
        "--mode", choices=RUN_MODES, default="managed", help="run mode (default: managed)"
    )
    parser.add_argument(
        "--n", type=int, default=None, help="article ceiling for the run (clamped to N_MAX)"
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    result = bootstrap(settings, run=not args.no_run, mode=args.mode, n=args.n)
    _emit(result)
    run_summary = result.get("run")
    if result.get("ran") and isinstance(run_summary, dict):
        return _EXIT.get(run_summary.get("status"), _EXIT["failed"])
    return 0


def _direct_main(argv: list[str], *, build_deps: DepsBuilder | None = None) -> int:
    """``censurado-brain direct --persona ID [--brief TEXT] [--link URL ...] [--focus TEXT]``:
    run mode 3, one persona writes one article from a BRIEF, BYPASSING the manager. The
    brief is a free-text instruction; ``--link`` may be repeated for 0..N sources the
    operator vouched for; ``--focus`` narrows the angle. The agent reads the links and
    researches OUTWARD from the brief, and the corroboration gate is OFF for this path.
    At least one of ``--brief`` / ``--link`` is required (there has to be something to
    write about). Prints a JSON summary and returns the run's exit code (the same code map
    as a batch run)."""
    parser = argparse.ArgumentParser(
        prog="censurado-brain direct",
        description="Write one article from a brief with one persona (bypasses the manager).",
    )
    parser.add_argument("--persona", required=True, help="the persona id that writes it")
    parser.add_argument(
        "--brief", default="", help="the free-text instruction for the article"
    )
    parser.add_argument(
        "--link", action="append", default=None, metavar="URL",
        help="a source URL to read (repeatable for 0..N links)",
    )
    parser.add_argument(
        "--focus", default=None, help="an optional focus that narrows the angle"
    )
    parser.add_argument(
        "--images", action=argparse.BooleanOptionalAction, default=None,
        help="generate a hero image for this article (default: the server setting)",
    )
    args = parser.parse_args(argv)

    links: list[str] = []
    for raw in args.link or []:
        cleaned = (raw or "").strip()
        if cleaned and cleaned not in links:
            links.append(cleaned)
    brief = (args.brief or "").strip()
    if not brief and not links:
        _emit({"run_id": None, "mode": "direct", "status": "failed",
               "error": "provide a --brief and/or at least one --link"})
        return _EXIT["failed"]

    settings = load_settings()
    deps = (build_deps or build_deps_from_env)(settings)
    try:
        if deps.persona_store.get(args.persona) is None:
            _emit({"run_id": None, "mode": "direct", "status": "failed",
                   "error": f"unknown persona {args.persona!r}"})
            return _EXIT["failed"]
        report = run_direct(
            deps=deps, links=links, persona_id=args.persona, brief=brief, focus=args.focus,
            images=args.images,
        )
    except Exception as exc:
        _emit({"run_id": None, "mode": "direct", "status": "failed", "error": str(exc)})
        return _EXIT["failed"]

    _emit(_summary(report))
    return _EXIT.get(report.status, _EXIT["failed"])


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
    """``censurado-brain sources list|add|update|remove|enable|disable``: curate the
    source (portal) registry from the command line, mirroring the HTTP management API.
    Operates on a local ``PortalStore`` over the brain DB. Prints a JSON result via
    ``_emit`` and returns 0 on success, 1 on a store rejection (duplicate domain, invalid
    feed_type, unknown id) or a missing/unknown sub-verb. ``store`` is injectable for tests."""
    if not argv:
        _emit({"error": "usage: sources list|add|update|remove|enable|disable"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("list", "add", "update", "remove", "enable", "disable"):
        _emit({"error": f"unknown sources verb {verb!r}"})
        return _EXIT["failed"]

    store = store or _open_portal_store()

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
        parser.add_argument("portal_id", help="the source id (e.g. clarin-com)")
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
        parser.add_argument("portal_id", help="the source id (e.g. clarin-com)")
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
    parser.add_argument("portal_id", help="the source id (e.g. clarin-com)")
    args = parser.parse_args(rest)
    removed = store.delete(args.portal_id)
    _emit({"id": args.portal_id, "removed": removed})
    return 0 if removed else _EXIT["failed"]


def _open_runs_store() -> RunStore:
    """Open the brain DB the API uses and wrap it in a ``RunStore``. The CLI run verbs are
    their own process (a separate connection from a live brain); WAL + the busy timeout
    (see ``open_db``) let that connection share the file safely."""
    settings = load_settings()
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    return RunStore(conn)


def _run_detail_payload(store: RunStore, run) -> dict:
    """One run plus its assignments, mirroring the HTTP ``GET /runs/{id}`` body: the
    run-level record and the per-assignment statuses (so a dropped/published article is
    visible from the CLI exactly as over HTTP)."""
    assignments = store.list_assignments(run_id=run.id)
    return {
        "run_id": run.id,
        "mode": run.mode,
        "status": run.status,
        "n_requested": run.n_requested,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "assignments": [
            {
                "id": a.id,
                "persona_id": a.persona_id,
                "section": a.section,
                "status": a.status,
                "published_id": a.published_id,
                "drop_reason": a.drop_reason,
                "image_url": a.image_url,
            }
            for a in assignments
        ],
    }


def _runs_main(argv: list[str], *, store: RunStore | None = None) -> int:
    """``censurado-brain runs list|get``: read-only inspection of the run records,
    mirroring the HTTP run surface. ``list`` prints every run newest-first (optionally
    filtered to a ``--status``), ``get <run_id>`` prints one run with its assignments.
    Operates on a local ``RunStore`` over the brain DB. Prints a JSON result via ``_emit``
    and returns 0 on success, 1 on an unknown run/sub-verb. ``store`` is injectable for
    tests."""
    if not argv:
        _emit({"error": "usage: runs list|get"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("list", "get"):
        _emit({"error": f"unknown runs verb {verb!r}"})
        return _EXIT["failed"]

    store = store or _open_runs_store()

    if verb == "list":
        parser = argparse.ArgumentParser(prog="censurado-brain runs list")
        parser.add_argument(
            "--status", default=None,
            help="filter to one status (running|done|done_with_errors|failed)",
        )
        args = parser.parse_args(rest)
        runs = store.list_runs(status=args.status)
        _emit({
            "runs": [
                {
                    "run_id": r.id,
                    "mode": r.mode,
                    "status": r.status,
                    "n_requested": r.n_requested,
                    "created_at": r.created_at,
                    "finished_at": r.finished_at,
                }
                for r in runs
            ],
            "total": len(runs),
        })
        return 0

    # verb == "get"
    parser = argparse.ArgumentParser(prog="censurado-brain runs get")
    parser.add_argument("run_id", help="the run id to inspect")
    args = parser.parse_args(rest)
    run = store.get_run(args.run_id)
    if run is None:
        _emit({"error": f"unknown run {args.run_id!r}"})
        return _EXIT["failed"]
    _emit(_run_detail_payload(store, run))
    return 0


def _open_persona_store() -> PersonaStore:
    """Open the brain DB the API uses and wrap it in a ``PersonaStore``. The CLI author
    verbs are their own process (a separate connection from a live brain); WAL + the busy
    timeout (see ``open_db``) let that connection share the file safely."""
    settings = load_settings()
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    return PersonaStore(conn)


def _persona_sources_payload(persona: Persona, portal_store: PortalStore) -> dict:
    """The author's source pool as a JSON-able dict, mirroring the HTTP
    ``PersonaSourcesOut``: the raw linked ids plus those resolved to the live portal
    rows (a stale id whose portal is gone stays in ``source_ids`` but not ``portals``)."""
    portals = [
        asdict(portal)
        for portal in (portal_store.get(pid) for pid in persona.sources)
        if portal is not None
    ]
    return {
        "persona_id": persona.id,
        "source_ids": list(persona.sources),
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
    parser.add_argument("portal_id", help="the source id (e.g. clarin-com)")
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
    (persona) registry from the command line WITHOUT a synthesis job, mirroring the HTTP
    management API. Operates on a local ``PersonaStore`` over the brain DB. Prints a JSON
    result via ``_emit`` and returns 0 on success, 1 on a store rejection (duplicate id,
    invalid beat, unknown id, an in-use delete) or a missing/unknown sub-verb. The
    ``sources`` sub-verb curates an author's source links and also needs the
    ``PortalStore`` to validate ids. Both stores are injectable for tests."""
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


def main(
    argv: list[str] | None = None,
    *,
    build_deps: DepsBuilder | None = None,
    portal_store: PortalStore | None = None,
    persona_store: PersonaStore | None = None,
    run_store: RunStore | None = None,
) -> int:
    """Parse args, run once, print a JSON summary, return an exit code.

    ``build_deps`` defaults to the production assembly; a test overrides it to inject
    in-process doubles for the network seams while still driving this real path."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "bootstrap":
        # The one-command setup path: seed the newsroom, then run. Kept as a subcommand
        # so the bare invocation (a plain --mode run) is unchanged for the trigger.
        return _bootstrap_main(argv[1:])
    if argv and argv[0] == "direct":
        # Run mode 3: write one article from a link, bypassing the manager. A subcommand
        # so the bare --mode invocation (the periodic trigger) is unchanged.
        return _direct_main(argv[1:], build_deps=build_deps)
    if argv and argv[0] == "mirror-authors":
        # The one-time author backfill: push local personas to the platform registry.
        # A subcommand so the bare --mode run path stays untouched.
        return _mirror_authors_main(argv[1:])
    if argv and argv[0] == "sources":
        # Curate the source (portal) registry, mirroring the HTTP management API. A
        # subcommand so the bare --mode run path (the periodic trigger) is unchanged.
        return _sources_main(argv[1:], store=portal_store)
    if argv and argv[0] == "authors":
        # Curate the author (persona) registry without a synthesis job, mirroring the
        # HTTP management API. A subcommand so the bare --mode run path is unchanged. The
        # portal store rides along so `authors sources` can validate source ids.
        return _authors_main(argv[1:], store=persona_store, portal_store=portal_store)
    if argv and argv[0] == "runs":
        # Inspect the run records (list / get), mirroring the HTTP run surface. A
        # subcommand so the bare --mode run path (the periodic trigger) is unchanged.
        return _runs_main(argv[1:], store=run_store)
    args = _parser().parse_args(argv)
    settings = load_settings()
    deps = (build_deps or build_deps_from_env)(settings)

    # Create the run record and execute it on THIS thread. A one-shot command runs
    # synchronously: there is no request to return to, unlike the HTTP surface's
    # 202-then-background path. Both the record creation and the run are guarded so
    # EVERY exit prints a parseable JSON summary: execute_run already records the run
    # failed, and a failure before the record exists reports a null run id rather than
    # crashing with only a traceback. (An assembly failure in build_deps above is a
    # config error that is left to crash loudly, distinct from a run failure.)
    run = None
    try:
        run, scope = start_run(
            args.mode, deps=deps, n=args.n,
            persona_ids=_parse_persona_ids(args.persona_ids), images=args.images,
        )
        report = execute_run(run=run, scope=scope, deps=deps)
    except Exception as exc:
        _emit({
            "run_id": run.id if run is not None else None,
            "mode": args.mode,
            "status": "failed",
            "error": str(exc),
        })
        return _EXIT["failed"]

    _emit(_summary(report))
    return _EXIT.get(report.status, _EXIT["failed"])
