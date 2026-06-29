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
from dataclasses import asdict, replace

from newsroom.bootstrap import bootstrap
from newsroom.config import Settings, load_settings
from newsroom.contracts.sections import SECTION_ENUM, is_valid_section
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
from newsroom.runner import (
    RUN_MODES,
    RunDeps,
    RunReport,
    build_run_deps,
    execute_run,
    run_batch,
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


def _batch_main(argv: list[str], *, build_deps: DepsBuilder | None = None) -> int:
    """``censurado-brain batch [--persona ID ...] [--timeframe day] [--max N] [--images]``:
    run mode 4, a BATCH sweep. The manager runs ONCE PER AUTHOR over that author's OWN
    source-scoped, time-filtered discovery search, so each desk lists topics from only the
    outlets it reads; the per-author manifests are merged and published through the same
    per-article pipeline as every other run.

    ``--persona`` is repeatable to narrow the desks (default: every active author);
    ``--timeframe`` is the discovery freshness window (``any|day|week|month|year``, default
    the server's ``batch_timeframe``); ``--max`` caps the whole batch (default: the natural
    ``batch_per_author x desks`` bound). Prints a JSON summary and returns the run's exit
    code (the same map as every other run verb)."""
    parser = argparse.ArgumentParser(
        prog="censurado-brain batch",
        description="Run a batch sweep: the manager fanned once per author over per-author sources.",
    )
    parser.add_argument(
        "--persona", action="append", default=None, metavar="ID",
        help="restrict the sweep to this author id (repeatable; default: all active authors)",
    )
    parser.add_argument(
        "--timeframe", default=None, choices=("any", "day", "week", "month", "year"),
        help="the discovery freshness window (default: the server's batch_timeframe)",
    )
    parser.add_argument(
        "--max", type=int, default=None, metavar="N",
        help="cap the total articles across all desks (default: batch_per_author x desks)",
    )
    parser.add_argument(
        "--images", action=argparse.BooleanOptionalAction, default=None,
        help="generate hero images for the batch (default: the server setting)",
    )
    args = parser.parse_args(argv)

    persona_ids: list[str] = []
    for raw in args.persona or []:
        cleaned = (raw or "").strip()
        if cleaned and cleaned not in persona_ids:
            persona_ids.append(cleaned)

    settings = load_settings()
    deps = (build_deps or build_deps_from_env)(settings)
    try:
        report = run_batch(
            deps=deps, persona_ids=persona_ids or None, timeframe=args.timeframe,
            max_total=args.max, images=args.images,
        )
    except Exception as exc:
        _emit({"run_id": None, "mode": "batch", "status": "failed", "error": str(exc)})
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
        parser.add_argument("portal_id", help="the source id (e.g. clarin-com)")
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


# A topics-cleanse seam bundle, injected by tests so the verb is driven end to end
# without a network or a model call (fetch the corpus, cluster the tags, apply the
# remap). In production each defaults to the real implementation over the live APIs.
TopicsFetch = Callable[[], list]
TopicsCluster = Callable[[list], dict]
TopicsApply = Callable[[list], tuple]


def _topics_main(
    argv: list[str],
    *,
    fetch: TopicsFetch | None = None,
    cluster: TopicsCluster | None = None,
    apply: TopicsApply | None = None,
) -> int:
    """``censurado-brain topics cleanse [--apply]``: the agentic topic cleanse. Reads the
    corpus tag set over the read API, asks the model to cluster synonymous/overlapping
    tags into a small canonical set, and (with ``--apply``) remaps each changed article in
    place over the operator edit lane (``PUT /articles``, admin:write). Default is a
    DRY-RUN that prints the canonical map + the per-article plan and writes nothing. Prints
    a JSON summary via ``_emit``; returns 0 on success, 2 if some applies failed, 1 on a
    read/usage error. The three seams are injectable so a test drives the verb without a
    network or a model."""
    if not argv or argv[0] != "cleanse":
        _emit({"error": "usage: topics cleanse [--apply]"})
        return _EXIT["failed"]
    parser = argparse.ArgumentParser(prog="censurado-brain topics cleanse")
    parser.add_argument(
        "--apply", action="store_true",
        help="apply the remap (default: dry-run, print the plan and write nothing)",
    )
    parser.add_argument(
        "--map-file", default=None, metavar="PATH",
        help="apply a pre-computed canonical map (JSON {tag: canonical}) from PATH (or '-' "
        "for stdin) INSTEAD of asking the model. This is how a CLI agent (e.g. Claude) "
        "supplies the clustering itself, with no inference backend in the loop.",
    )
    args = parser.parse_args(argv[1:])

    from newsroom.cleanse import (
        apply_remap,
        cluster_topics,
        collect_topics,
        fetch_articles,
        remap_plan,
    )

    settings = load_settings()
    base = str(settings.publish_base_url).rstrip("/")
    read_token = settings.operator_token
    edit_token = settings.admin_token or settings.operator_token

    fetch = fetch or (lambda: fetch_articles(base, read_token))
    cluster = cluster or (lambda tags: cluster_topics(tags))
    apply = apply or (lambda plan: apply_remap(plan, base_url=base, read_token=read_token, edit_token=edit_token))

    try:
        articles = fetch()
    except Exception as exc:  # a read failure is a clean usage error, not a crash
        _emit({"error": f"failed to read the corpus: {exc}"})
        return _EXIT["failed"]

    tags = collect_topics(articles)
    if args.map_file:
        # The agent-supplied path: read a {tag: canonical} map (file or '-' stdin) and use
        # it directly, so the clustering can come from a CLI agent rather than a backend
        # model. Every corpus tag maps to itself unless the map overrides it.
        try:
            src = sys.stdin.read() if args.map_file == "-" else open(args.map_file, encoding="utf-8").read()
            raw = json.loads(src)
            if not isinstance(raw, dict):
                raise ValueError("map must be a JSON object {tag: canonical}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _emit({"error": f"failed to read --map-file: {exc}"})
            return _EXIT["failed"]
        canon = {t: (str(raw.get(t, t)).strip() or t) for t in tags}
    else:
        try:
            canon = cluster(tags)
        except Exception as exc:  # an inference failure (e.g. 429) exits cleanly, not a traceback
            _emit({"error": f"clustering failed (inference): {exc}"})
            return _EXIT["failed"]
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


# A synth takes a seed plus the persona store and the loaded settings and returns the new
# persona id (running the model call synchronously). Injected so the `authors synthesize`
# verb tests without a real model call, exactly like the run/probe seams.
SynthesizeFn = Callable[..., str]


def _default_synthesize(seed, *, store: PersonaStore, settings: Settings) -> str:
    """The production synth: build the persona-synth provider config from settings (the
    same ``ProviderConfig`` the HTTP synthesis route assembles) and run one synthesis
    SYNCHRONOUSLY, returning the new persona id. A CLI process has no request to return to,
    so unlike the 202-then-poll HTTP route this blocks on the model and writes the persona
    before returning."""
    from newsroom.brain.synthesis import synthesize_persona
    from newsroom.inference.provider import (
        DEFAULT_MODEL,
        DEFAULT_PROVIDER,
        DIALECTS,
        ProviderConfig,
    )

    cfg = ProviderConfig(
        role="persona_synth",
        provider=DEFAULT_PROVIDER,
        base_url=str(settings.inference_base_url).rstrip("/"),
        model=DEFAULT_MODEL,
        **DIALECTS[DEFAULT_PROVIDER],
    )
    return synthesize_persona(seed, cfg=cfg, store=store, prompts_dir=settings.prompts_dir)


def _authors_synthesize_main(
    argv: list[str], *, store: PersonaStore, synthesize: SynthesizeFn | None = None
) -> int:
    """``censurado-brain authors synthesize --display-name --beat --seed [--sources]``: grow
    a persona from a free-text SEED brief via one model call, the CLI parity of the async
    ``POST /personas`` synthesis. The beat is validated UP FRONT against the section enum
    (rejected before any model call, like the HTTP route), then the model drafts the voice
    and the persona is persisted. Runs synchronously (no job to poll). Prints the resulting
    ``PersonaOut`` (the stored persona) via ``_emit`` and returns 0; on a synthesis failure
    (bad model output, duplicate id) prints a JSON error and returns 1. ``synthesize`` is
    injectable for tests."""
    from newsroom.brain.synthesis import PersonaSeed, SynthesisError

    parser = argparse.ArgumentParser(prog="censurado-brain authors synthesize")
    parser.add_argument("--display-name", required=True, help="the author's display name")
    parser.add_argument("--beat", required=True, help=f"one of {', '.join(SECTION_ENUM)}")
    parser.add_argument(
        "--seed", required=True, help="the free-text brief the model grows into a voice"
    )
    parser.add_argument(
        "--sources", default=None, help="comma-separated source ids/urls the persona seeds with"
    )
    args = parser.parse_args(argv)

    if not is_valid_section(args.beat):  # reject before any model call, like POST /personas
        _emit({"error": f"invalid beat {args.beat!r}; must be one of {', '.join(SECTION_ENUM)}"})
        return _EXIT["failed"]

    seed = PersonaSeed(
        display_name=args.display_name,
        beat=args.beat,
        seed=args.seed,
        sources=_split_csv(args.sources),
    )
    settings = load_settings()
    synth = synthesize or _default_synthesize
    try:
        persona_id = synth(seed, store=store, settings=settings)
    except SynthesisError as exc:  # bad model output or a store conflict: a clean exit 1
        _emit({"error": str(exc)})
        return _EXIT["failed"]
    persona = store.get(persona_id)
    _emit(asdict(persona))
    return 0


def _authors_main(
    argv: list[str],
    *,
    store: PersonaStore | None = None,
    portal_store: PortalStore | None = None,
    synthesize: SynthesizeFn | None = None,
) -> int:
    """``censurado-brain authors list|get|add|synthesize|update|remove|sources``: curate the
    author (persona) registry from the command line, mirroring the HTTP management API.
    ``add`` builds a persona from EXPLICIT fields with no model call (POST /personas/direct);
    ``synthesize`` drafts a voice from a free-text seed brief via ONE model call (the CLI
    parity of the async POST /personas synthesis, run synchronously so it needs no job
    poll). Operates on a local ``PersonaStore`` over the brain DB. Prints a JSON result via
    ``_emit`` and returns 0 on success, 1 on a store rejection (duplicate id, invalid beat,
    unknown id, an in-use delete), a synthesis failure, or a missing/unknown sub-verb. The
    ``sources`` sub-verb curates an author's source links and also needs the ``PortalStore``
    to validate ids. The stores and the ``synthesize`` callable are injectable for tests."""
    if not argv:
        _emit({"error": "usage: authors list|get|add|synthesize|update|remove|sources"})
        return _EXIT["failed"]
    verb, rest = argv[0], argv[1:]
    if verb not in ("list", "get", "add", "synthesize", "update", "remove", "sources"):
        _emit({"error": f"unknown authors verb {verb!r}"})
        return _EXIT["failed"]

    store = store or _open_persona_store()

    if verb == "sources":
        return _authors_sources_main(rest, persona_store=store, portal_store=portal_store)

    if verb == "synthesize":
        return _authors_synthesize_main(rest, store=store, synthesize=synthesize)

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
    build_deps: DepsBuilder | None = None,
    portal_store: PortalStore | None = None,
    persona_store: PersonaStore | None = None,
    run_store: RunStore | None = None,
    style_store: StyleStore | None = None,
    location_store: LocationStore | None = None,
    backend_probe: BackendProbeFn | None = None,
    synthesize: SynthesizeFn | None = None,
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
    if argv and argv[0] == "batch":
        # Run mode 4: a batch sweep, the manager fanned once per author over per-author
        # sources. A subcommand so the bare --mode run path (the periodic trigger) is
        # unchanged.
        return _batch_main(argv[1:], build_deps=build_deps)
    if argv and argv[0] == "mirror-authors":
        # The one-time author backfill: push local personas to the platform registry.
        # A subcommand so the bare --mode run path stays untouched.
        return _mirror_authors_main(argv[1:])
    if argv and argv[0] == "status":
        # Report the brain<->backend connection (live probe), the CLI parity of
        # GET /status/backend. A subcommand so the bare --mode run path is unchanged.
        return _status_main(argv[1:], probe=backend_probe)
    if argv and argv[0] == "sources":
        # Curate the source (portal) registry, mirroring the HTTP management API. A
        # subcommand so the bare --mode run path (the periodic trigger) is unchanged.
        return _sources_main(argv[1:], store=portal_store)
    if argv and argv[0] == "authors":
        # Curate the author (persona) registry, mirroring the HTTP management API (direct
        # CRUD AND the `synthesize` model path). A subcommand so the bare --mode run path is
        # unchanged. The portal store rides along so `authors sources` can validate source
        # ids; `synthesize` is injectable so a test drives it without a real model call.
        return _authors_main(
            argv[1:], store=persona_store, portal_store=portal_store, synthesize=synthesize
        )
    if argv and argv[0] == "runs":
        # Inspect the run records (list / get), mirroring the HTTP run surface. A
        # subcommand so the bare --mode run path (the periodic trigger) is unchanged.
        return _runs_main(argv[1:], store=run_store)
    if argv and argv[0] == "editorial":
        # Read and edit the operator-owned editorial config (house style + location),
        # mirroring the HTTP editorial API. A subcommand so the bare --mode run path is
        # unchanged. The style/location stores ride along so a test can inject them.
        return _editorial_main(
            argv[1:], style_store=style_store, location_store=location_store
        )
    if argv and argv[0] == "topics":
        # Agentic topic cleanse: cluster the corpus tag set into a canonical set and
        # remap changed articles. A subcommand so the bare --mode run path is unchanged.
        return _topics_main(argv[1:])
    if argv and argv[0] == "embeds":
        # Re-check stored tweet/youtube snapshots and refresh their availability flags
        # (deleted tweet -> erased, pulled video -> unavailable). A subcommand so the bare
        # --mode run path is unchanged.
        return _embeds_main(argv[1:])
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
