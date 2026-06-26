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

from newsroom.bootstrap import bootstrap
from newsroom.config import Settings, load_settings
from newsroom.db import open_db
from newsroom.personas import PersonaStore
from newsroom.runner import (
    RUN_MODES,
    RunDeps,
    RunReport,
    build_run_deps,
    execute_run,
    run_direct,
    start_run,
)

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
    """``censurado-brain direct --url URL --persona ID [--brief TEXT]``: run mode 3,
    one persona writes one article seeded from a link, BYPASSING the manager. Prints a
    JSON summary and returns the run's exit code (the same code map as a batch run)."""
    parser = argparse.ArgumentParser(
        prog="censurado-brain direct",
        description="Write one article from a link with one persona (bypasses the manager).",
    )
    parser.add_argument("--url", required=True, help="the source URL to write about")
    parser.add_argument("--persona", required=True, help="the persona id that writes it")
    parser.add_argument(
        "--brief", default=None,
        help="the angle/brief for the article (default: a generic 'write about this source')",
    )
    parser.add_argument(
        "--images", action=argparse.BooleanOptionalAction, default=None,
        help="generate a hero image for this article (default: the server setting)",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    deps = (build_deps or build_deps_from_env)(settings)
    try:
        if deps.persona_store.get(args.persona) is None:
            _emit({"run_id": None, "mode": "direct", "status": "failed",
                   "error": f"unknown persona {args.persona!r}"})
            return _EXIT["failed"]
        report = run_direct(
            deps=deps, url=args.url, persona_id=args.persona, brief=args.brief, images=args.images
        )
    except Exception as exc:
        _emit({"run_id": None, "mode": "direct", "status": "failed", "error": str(exc)})
        return _EXIT["failed"]

    _emit(_summary(report))
    return _EXIT.get(report.status, _EXIT["failed"])


def main(argv: list[str] | None = None, *, build_deps: DepsBuilder | None = None) -> int:
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
